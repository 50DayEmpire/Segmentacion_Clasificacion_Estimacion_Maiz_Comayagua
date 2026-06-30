# pipeline/__init__.py
from .ingesta import obtener_datacube_indices_crudo, obtener_datos_climaticos_crudo
from .modulo_vpm import preprocesar_indices_vpm, calcular_gpp_vpm
from .modulo_fenologico import detectar_sos