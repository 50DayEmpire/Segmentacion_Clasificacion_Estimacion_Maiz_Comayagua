import pandas as pd
import numpy as np
from whittaker_eilers import WhittakerSmoother

def aplicar_whittaker_series(
    diccionario_dfs: dict, 
    lambda_param: float = 10000.0, 
    orden: int = 2
) -> dict:
    """
    Aplica el algoritmo de suavizado y relleno de gaps Whittaker sobre un 
    diccionario de DataFrames de Pandas indexados diariamente.
    
    Parameters
    ----------
    diccionario_dfs : dict[str, pd.DataFrame]
        Clave: Nombre del índice ("EVI", "LSWI"). 
        Valor: DataFrame con DatetimeIndex DIARIO lleno de NaN en los días vacíos.
    lambda_param : float
        Parámetro de penalización de rugosidad (Sugerido para S2: 10000.0).
    orden : int
        Orden de las diferencias finitas (típicamente 2 para curvas fenológicas).
        
    Returns
    -------
    dict[str, pd.DataFrame]
        Misma estructura de entrada, pero con las series suavizadas y sin NaNs.
    """
    dict_suavizado = {}
    
    for nombre_indice, df_crudo in diccionario_dfs.items():
        print(f"📈 Suavizando serie temporal para: {nombre_indice}...")
        
        # Clonamos la estructura para no sobreescribir el DataFrame original
        df_resultado = df_crudo.copy()
        num_filas = len(df_crudo.index)
        
        # Inicializar el objeto Whittaker (de la librería whittaker-eilers)
        whittaker = WhittakerSmoother(lmbda=lambda_param, order=orden, data_length=num_filas,weights=pesos)
        
        # Iterar parcela por parcela (columna por columna)
        for parcela in df_crudo.columns:
            serie_valores = df_crudo[parcela].values
            
            # 📌 CLAVE: Whittaker necesita una matriz de pesos (w).
            # 1.0 para datos reales del satélite, 0.0 para nubes o días vacíos (NaN)
            pesos = np.where(np.isnan(serie_valores), 0.0, 1.0)
            
            # Reemplazar temporalmente los NaN por 0.0 solo para que el input numérico sea válido
            valores_preparados = np.nan_to_num(serie_valores, nan=0.0)
            
            # Ejecutar el algoritmo
            valores_suaves = whittaker.smooth(valores_preparados)
            
            # Guardar la serie diaria resultante en nuestro DataFrame
            df_resultado[parcela] = valores_suaves
            
        dict_suavizado[nombre_indice] = df_resultado
        
    return dict_suavizado