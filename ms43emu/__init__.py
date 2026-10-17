"""Emulation of the Siemens MS43 DME (C167CS, BMW M54) from a full flash read."""
from c166.cpu import Cpu, CpuError

from .image import Image, load_image, load_file, find_image
from .machine import Machine
