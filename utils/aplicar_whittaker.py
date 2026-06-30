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
        
        df_resultado = df_crudo.copy()
        
        # Iterar parcela por parcela
        for parcela in df_crudo.columns:
            serie_valores = df_crudo[parcela].values
            
            # Generar pesos (0 para NaN, 1 para válidos)
            pesos = np.where(np.isnan(serie_valores), 0.0, 1.0).tolist()
            valores_preparados = np.nan_to_num(serie_valores, nan=0.0).tolist()
            
            # 💡 CORRECCIÓN: Se inicializa un suavizador por cada combinación única 
            # de serie/pesos de la parcela, validando que existan suficientes datos.
            num_validos = np.sum(~np.isnan(serie_valores))
            if num_validos < (orden + 1):
                print(f"⚠️ La parcela '{parcela}' tiene menos de {orden + 1} observaciones válidas ({num_validos}). Se aplica interpolación lineal.")
                valores_suaves = pd.Series(serie_valores).interpolate(method="linear", limit_direction="both").fillna(0.0).values
            else:
                try:
                    whittaker = WhittakerSmoother(
                        lmbda=lambda_param, 
                        order=orden, 
                        data_length=len(valores_preparados),
                        x_input=None,       # Al ser equiespaciado diario es None
                        weights=pesos       # ¡Aquí van los pesos de la parcela!
                    )
                    valores_suaves = whittaker.smooth(valores_preparados)
                except Exception as e:
                    print(f"⚠️ Whittaker falló para la parcela '{parcela}': {str(e)}. Se aplica interpolación lineal.")
                    valores_suaves = pd.Series(serie_valores).interpolate(method="linear", limit_direction="both").fillna(0.0).values
            
            df_resultado[parcela] = valores_suaves
            
        dict_suavizado[nombre_indice] = df_resultado
        
    return dict_suavizado