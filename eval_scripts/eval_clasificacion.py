from rasterio.features import rasterize
from sklearn.metrics import confusion_matrix, classification_report

# =========================================================================
# 3. PIPELINE DE VALIDACIÓN (MATRIZ DE CONFUSIÓN A NIVEL DE PÍXEL)
# =========================================================================

def eval_clasificacion(gdf_poligonos, xarray_raster, clase_valor=1):
    """
    Compara los píxeles predichos por un modelo contra los polígonos de control.
    Genera métricas globales exactas basadas en área/píxeles.
    """
    # 1. Crear un ráster en blanco con las mismas dimensiones del modelo
    raster_shape = xarray_raster.shape[1:]
    transform = xarray_raster.rio.transform()

    # 2. Rasterizar tus polígonos manuales (Polígonos = 1, Fondo/No-Maíz = 0)
    geometrias = [(geom, 1) for geom in gdf_poligonos.geometry]
    gt_rasterized = rasterize(
        geometrias,
        out_shape=raster_shape,
        transform=transform,
        fill=0,
        dtype=np.uint8
    )

    # 3. Extraer la matriz de predicción del modelo (aplanar a 1D para sklearn)
    # Convertimos a binario usando el umbral si el ráster es probabilístico o directo si ya es clasificado
    pred_matrix = xarray_raster.values[0]

    # Si el modelo es probabilístico (0-200 o 0.0-1.0), binorizar aquí:
    if pred_matrix.max() > 1 and pred_matrix.dtype != np.uint8:
        pred_raster = (pred_matrix >= 0.2).astype(np.uint8) # Umbral 20%
    else:
        pred_raster = (pred_matrix == clase_valor).astype(np.uint8)

    # Aplanar las matrices para la evaluación estadística
    y_true = gt_rasterized.flatten()
    y_pred = pred_raster.flatten()

    # 4. Calcular Matriz de Confusión estándar
    matrix = confusion_matrix(y_true, y_pred, labels=[0, 1])
    tn, fp, fn, tp = matrix.ravel()

    # 5. Resolver Métricas del Benchmark
    recall = tp / (tp + fn) * 100
    precision = tp / (tp + fp) * 100
    f1_score = 2 * (precision * recall) / (precision + recall)
    iou = tp / (tp + fp + fn) * 100

    return {
        "TP": tp, "FP": fp, "FN": fn, "TN": tn,
        "Recall": recall, "Precision": precision,
        "F1": f1_score, "IoU": iou
    }