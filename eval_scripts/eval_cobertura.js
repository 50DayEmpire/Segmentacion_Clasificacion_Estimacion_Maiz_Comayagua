//Copiar y pegar en un nuevo script vacío en el editor web de GEE
//Requiere tener importado en el script el archivo de parcelas Ground Truth Palmerola
//y un polígono de aoi que delimite la región a evaluar.

Map.setOptions('HYBRID');

// 1. Configurar los parámetros de Dynamic World
// Definimos un rango de fechas
var fecha_inicio = '2025-01-01';
var fecha_fin = '2025-12-31';
var aoi = MuestraPalmerola;

// Cargar la colección de Dynamic World filtrada por parcelas y fechas
var dw = ee.ImageCollection('GOOGLE/DYNAMICWORLD/V1')
  .filterBounds(ParcelasGT)
  .filterDate(fecha_inicio, fecha_fin);

// Crear una composición utilizando la mediana para reducir las nubes y variaciones estacionales
var dw_mediana = dw.median().clip(aoi);

// Dynamic World tiene una banda para cada clase con valores de probabilidad entre 0 y 1.
// Extraemos la banda de cultivos ('crops')
var probabilidad_cultivos = dw_mediana.select('crops');

// OPCIONAL: Crear una máscara binaria. 
// Por ejemplo, consideraremos como "cultivo válido" si la probabilidad es mayor al 20% (0.2)
var mascara_cultivos = probabilidad_cultivos.gte(0.2);

// Visualización en el mapa
Map.addLayer(mascara_cultivos.selfMask(), {palette: ['#e3b878', '#b8860b']}, "Máscara de Cultivos (>20% prob)", 1, 0.6);
Map.addLayer(ParcelasGT, {color: "red"}, "Muestra de parcelas de referencia", 1, 0.5);

Map.centerObject(ParcelasGT);

// 2. Mapear la función sobre FeatureCollection (ParcelasGT)
var resultados = ParcelasGT.map(function(feat) {
  
  // Renombramos la banda para mantener un control estricto en el diccionario
  var imgTP = ee.Image.pixelArea().updateMask(mascara_cultivos).rename('area_cultivos');
  
  var areaMask = imgTP.reduceRegion({
      reducer: ee.Reducer.sum(),
      geometry: feat.geometry(),
      scale: 10, // Resolución nativa de Dynamic World y Sentinel-2
      maxPixels: 1e13
    });
  
  // Calculamos el área total del polígono usando la geometría ráster a escala 10m
  var areaPolyRaster = ee.Image.pixelArea().rename('area_real').reduceRegion({
    reducer: ee.Reducer.sum(),
    geometry: feat.geometry(),
    scale: 10,
    maxPixels: 1e13
    });
  
  // Extraemos los valores del reduceRegion
  var areaInter = ee.Number(areaMask.get('area_cultivos'));
  var areaPolyRasterVal = ee.Number(areaPolyRaster.get('area_real'));
  
  return feat.set({
    'area_poly_m2': areaPolyRasterVal,
    'area_cultivo_m2': areaInter
  });
});

//===================Medir Precisión en la Zona de Muestra================================
// 1. Obtener el TP total sumando el área acertada de todas las parcelas individuales
var tp_total_m2 = ee.Number(resultados.aggregate_sum('area_cultivo_m2'));

// 2. Calcular TODO lo que el satélite predijo como cultivo en el área de muestra (aoi)
// Usamos .updateMask para sumar solo los píxeles activados como cultivo
var imgPrediccionTotal = ee.Image.pixelArea().updateMask(mascara_cultivos).rename('prediccion_m2');

var reduccionTotal = imgPrediccionTotal.reduceRegion({
  reducer: ee.Reducer.sum(),
  geometry: aoi,
  scale: 10,
  maxPixels: 1e13
});

var prediccion_total_m2 = ee.Number(reduccionTotal.get('prediccion_m2'));

// 3. Calcular Falsos Positivos (Lo que predijo fuera de las parcelas)
var fp_total_m2 = prediccion_total_m2.subtract(tp_total_m2);

// 4. Calcular la Precisión 
var precision = ee.Algorithms.If(
  prediccion_total_m2.gt(0),
  tp_total_m2.divide(prediccion_total_m2).multiply(100),
  ee.Number(100) // Si no predijo nada, la precisión teórica es 100% (no cometió errores FP)
);

//5. Obtener Falsos Negativos
var FN = ee.Number(resultados.aggregate_sum('area_poly_m2')).subtract(tp_total_m2);

//6. Calcular Recall
var recall = tp_total_m2.divide(tp_total_m2.add(FN)).multiply(100);

//7. Calcular Intersección sobre la Unión (IoU)
var union = tp_total_m2.add(fp_total_m2).add(FN);
var iou = tp_total_m2.divide(union).multiply(100);

//================Cálculo de F1-Score=====================

precision = ee.Number(precision)

// 2. Sumamos ambas métricas para controlar la división por cero
var suma_metricas = precision.add(recall);

// 3. Aplicamos la fórmula de la media armónica mediante objetos del servidor de GEE
var f1_score = ee.Algorithms.If(
  suma_metricas.gt(0),
  ee.Number(2).multiply(precision.multiply(recall)).divide(suma_metricas),
  ee.Number(0)
);

//=================Resultados========================
print('==================================================================');
print('                REPORTE DE VALIDACIÓN GLOBAL                      ');
print('==================================================================');
print('Área Total Predicha como Cultivo por DW (m²):', prediccion_total_m2);
print('Verdaderos Positivos - TP Total (m²):', tp_total_m2);
print('Falsos Positivos - FP Total (m²):', fp_total_m2);
print('------------------------------------------------------------------');
print('RECALL (%):', recall);
print('PRECISIÓN (%):', precision);
print('F1-SCORE/Coeficiente de Dice (%):', f1_score);
print('ÍNDICE IoU (Jaccard) (%):', iou);
print('==================================================================');
