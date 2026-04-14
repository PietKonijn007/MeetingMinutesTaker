"""Hardware detection and model recommendation for local AI workloads.

Detects GPU type, available VRAM/RAM, and recommends appropriate models
for both transcription (Whisper) and summarization (Ollama).
"""

from __future__ import annotations

import logging
import platform
import shutil
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class GPUInfo:
    name: str = "CPU"
    type: str = "cpu"  # "cuda", "metal", "cpu"
    vram_gb: float = 0.0
    compute_capability: str = ""


@dataclass
class HardwareProfile:
    gpu: GPUInfo = field(default_factory=GPUInfo)
    total_ram_gb: float = 0.0
    available_ram_gb: float = 0.0
    cpu_cores: int = 0
    platform: str = ""
    arch: str = ""


@dataclass
class ModelRecommendation:
    """Recommended model configuration based on hardware."""
    whisper_model: str = "medium"
    whisper_device: str = "cpu"
    whisper_compute_type: str = "int8"
    ollama_models: list[str] = field(default_factory=list)
    max_ollama_model_gb: float = 0.0
    notes: list[str] = field(default_factory=list)


def detect_hardware() -> HardwareProfile:
    """Detect available hardware capabilities."""
    profile = HardwareProfile(
        platform=platform.system(),
        arch=platform.machine(),
    )

    # CPU cores
    try:
        import os
        profile.cpu_cores = os.cpu_count() or 1
    except Exception:
        profile.cpu_cores = 1

    # System RAM
    try:
        import psutil
        mem = psutil.virtual_memory()
        profile.total_ram_gb = mem.total / (1024 ** 3)
        profile.available_ram_gb = mem.available / (1024 ** 3)
    except ImportError:
        # Fallback: try /proc/meminfo on Linux
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        kb = int(line.split()[1])
                        profile.total_ram_gb = kb / (1024 ** 2)
                    elif line.startswith("MemAvailable:"):
                        kb = int(line.split()[1])
                        profile.available_ram_gb = kb / (1024 ** 2)
        except Exception:
            pass

    # Apple Silicon detection
    if profile.platform == "Darwin" and profile.arch == "arm64":
        profile.gpu = GPUInfo(
            name=f"Apple Silicon ({platform.processor() or 'M-series'})",
            type="metal",
            # Unified memory — GPU can use most of system RAM
            vram_gb=profile.total_ram_gb * 0.75,
        )
        return profile

    # NVIDIA GPU detection
    try:
        import torch
        if torch.cuda.is_available():
            gpu_name = torch.cuda.get_device_name(0)
            vram = torch.cuda.get_device_properties(0).total_mem / (1024 ** 3)
            capability = torch.cuda.get_device_capability(0)
            profile.gpu = GPUInfo(
                name=gpu_name,
                type="cuda",
                vram_gb=vram,
                compute_capability=f"{capability[0]}.{capability[1]}",
            )
            return profile
    except ImportError:
        pass

    # No GPU found
    profile.gpu = GPUInfo(name="CPU", type="cpu", vram_gb=0.0)
    return profile


def recommend_models(profile: HardwareProfile) -> ModelRecommendation:
    """Recommend appropriate models based on detected hardware."""
    rec = ModelRecommendation()
    gpu = profile.gpu
    effective_vram = gpu.vram_gb if gpu.type != "cpu" else profile.available_ram_gb * 0.7

    # --- Whisper recommendations ---
    if gpu.type == "metal":
        rec.whisper_device = "auto"
        rec.whisper_compute_type = "float16"
        rec.whisper_model = "large-v3"
        rec.notes.append(f"Apple Silicon detected ({gpu.name}) — using Metal acceleration")
    elif gpu.type == "cuda":
        rec.whisper_device = "cuda"
        rec.whisper_compute_type = "float16"
        if gpu.vram_gb >= 6:
            rec.whisper_model = "large-v3"
        elif gpu.vram_gb >= 4:
            rec.whisper_model = "medium"
        else:
            rec.whisper_model = "small"
        rec.notes.append(f"NVIDIA GPU detected ({gpu.name}, {gpu.vram_gb:.1f}GB VRAM)")
    else:
        rec.whisper_device = "cpu"
        rec.whisper_compute_type = "int8"
        if profile.total_ram_gb >= 16:
            rec.whisper_model = "medium"
        elif profile.total_ram_gb >= 8:
            rec.whisper_model = "small"
        else:
            rec.whisper_model = "base"
        rec.notes.append(f"CPU mode — {profile.total_ram_gb:.0f}GB RAM, {profile.cpu_cores} cores")

    # --- Ollama model recommendations ---
    # Approximate VRAM/RAM needed for popular models at Q4 quantization:
    #   7B  → ~4-5GB
    #   14B → ~8-10GB
    #   32B → ~18-22GB
    #   70B → ~40-45GB
    rec.max_ollama_model_gb = effective_vram

    if effective_vram >= 45:
        rec.ollama_models = ["qwen2.5:72b", "llama3.1:70b", "qwen2.5:32b", "qwen2.5:14b", "qwen2.5:7b"]
        rec.notes.append("Enough memory for 70B+ models — near cloud-quality summarization")
    elif effective_vram >= 20:
        rec.ollama_models = ["qwen2.5:32b", "qwen2.5:14b", "mistral-small:24b", "qwen2.5:7b"]
        rec.notes.append("Enough memory for 32B models — good quality summarization")
    elif effective_vram >= 10:
        rec.ollama_models = ["qwen2.5:14b", "phi4:14b", "llama3.1:8b", "qwen2.5:7b"]
        rec.notes.append("Enough memory for 14B models — decent summarization")
    elif effective_vram >= 5:
        rec.ollama_models = ["qwen2.5:7b", "llama3.1:8b", "phi4:3.8b", "gemma2:2b"]
        rec.notes.append("Enough memory for 7B models — basic summarization")
    elif effective_vram >= 3:
        rec.ollama_models = ["phi4:3.8b", "gemma2:2b", "qwen2.5:3b"]
        rec.notes.append("Limited memory — only small models recommended")
    else:
        rec.ollama_models = []
        rec.notes.append("Insufficient memory for local LLM — use cloud providers instead")

    return rec


def check_ollama_available() -> dict:
    """Check if Ollama is installed and running.

    Returns a dict with status info:
      {"installed": bool, "running": bool, "version": str | None, "url": str}
    """
    import os

    base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
    result = {
        "installed": False,
        "running": False,
        "version": None,
        "url": base_url,
    }

    # Check if ollama binary exists
    result["installed"] = shutil.which("ollama") is not None

    # Check if the server is responding
    try:
        import httpx
        with httpx.Client(timeout=3) as client:
            resp = client.get(f"{base_url}/api/version")
            if resp.status_code == 200:
                result["running"] = True
                result["version"] = resp.json().get("version")
    except Exception:
        pass

    return result
