# tests/test_persistencia_gpp_diario.py
"""
Verifica que la suma del GPP diario persistido en series_diarias_vpm.gpp_diario
coincide con el gpp_acumulado almacenado en predicciones_ventana.
"""
import pytest
import numpy as np
import pandas as pd
from pipeline.ingesta import guardar_gpp_diario


def test_guardar_gpp_diario_suma_coincide_con_entrada(conn_prueba):
    """
    guardar_gpp_diario recibe un DataFrame de GPP diario.
    La suma de lo persistido en BD debe igualar la suma del DataFrame original.
    """
    rng = np.random.default_rng(42)
    fechas = pd.date_range("2024-04-01", periods=120, freq="D")
    valores = rng.uniform(2.0, 5.0, size=120)
    df_gpp = pd.DataFrame({"id_1": valores}, index=fechas)
    dfs_gpp = {"GPP": df_gpp}

    suma_esperada = float(valores.sum())
    n_filas = guardar_gpp_diario(dfs_gpp)

    assert n_filas == 120

    fila = conn_prueba.execute(
        "SELECT SUM(gpp_diario) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()
    assert fila[0] == pytest.approx(suma_esperada, rel=1e-10)


def test_guardar_gpp_diario_varias_parcelas(conn_prueba):
    """
    Múltiples parcelas en el mismo DataFrame: cada parcela se persiste
    correctamente y la suma global coincide.
    """
    conn_prueba.execute("INSERT INTO parcelas_vigentes (id_parcela, area_ha) VALUES (2, 5.0)")

    rng = np.random.default_rng(99)
    fechas = pd.date_range("2024-04-01", periods=60, freq="D")
    df_gpp = pd.DataFrame({
        "id_1": rng.uniform(2.0, 5.0, size=60),
        "id_2": rng.uniform(1.5, 4.0, size=60),
    }, index=fechas)
    dfs_gpp = {"GPP": df_gpp}

    guardar_gpp_diario(dfs_gpp)

    for col, id_parcela in [("id_1", 1), ("id_2", 2)]:
        suma_esperada = float(df_gpp[col].sum())
        fila = conn_prueba.execute(
            "SELECT SUM(gpp_diario) FROM series_diarias_vpm WHERE id_parcela = ?",
            (id_parcela,),
        ).fetchone()
        assert fila[0] == pytest.approx(suma_esperada, rel=1e-10)


def test_guardar_gpp_diario_con_nan(conn_prueba):
    """
    NaN en la serie no se persisten, y la suma en BD omite esas filas.
    """
    fechas = pd.date_range("2024-04-01", periods=10, freq="D")
    valores = np.full(10, 3.0)
    valores[3] = np.nan
    valores[7] = np.nan
    df_gpp = pd.DataFrame({"id_1": valores}, index=fechas)
    dfs_gpp = {"GPP": df_gpp}

    guardar_gpp_diario(dfs_gpp)

    n_filas = conn_prueba.execute(
        "SELECT COUNT(*) FROM series_diarias_vpm WHERE id_parcela = 1 AND gpp_diario IS NOT NULL"
    ).fetchone()[0]
    assert n_filas == 8

    suma_esperada = float(np.nansum(valores))
    fila = conn_prueba.execute(
        "SELECT SUM(gpp_diario) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()
    assert fila[0] == pytest.approx(suma_esperada, rel=1e-10)


def test_guardar_gpp_diario_dataframe_vacio(conn_prueba):
    """DataFrame vacío retorna 0 y no escribe nada."""
    df_gpp = pd.DataFrame({"id_1": []}, index=pd.DatetimeIndex([]))
    dfs_gpp = {"GPP": df_gpp}

    n = guardar_gpp_diario(dfs_gpp)
    assert n == 0

    filas = conn_prueba.execute(
        "SELECT COUNT(*) FROM series_diarias_vpm"
    ).fetchone()[0]
    assert filas == 0


def test_guardar_gpp_diario_sin_clave_gpp_lanza_error(conn_prueba):
    """Dict sin la clave 'GPP' debe lanzar KeyError."""
    with pytest.raises(KeyError, match="GPP"):
        guardar_gpp_diario({"PAR": pd.DataFrame()})


def test_guardar_gpp_diario_upsert_no_duplica(conn_prueba):
    """
    Llamar dos veces con los mismos datos no duplica filas
    y los valores existentes se preservan.
    """
    fechas = pd.date_range("2024-04-01", periods=5, freq="D")
    df1 = pd.DataFrame({"id_1": [1.0, 2.0, 3.0, 4.0, 5.0]}, index=fechas)
    guardar_gpp_diario({"GPP": df1})

    df2 = pd.DataFrame({"id_1": [9.0, 9.0, 9.0, 9.0, 9.0]}, index=fechas)
    guardar_gpp_diario({"GPP": df2})

    n_filas = conn_prueba.execute(
        "SELECT COUNT(*) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()[0]
    assert n_filas == 5

    fila = conn_prueba.execute(
        "SELECT SUM(gpp_diario) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()
    assert fila[0] == pytest.approx(1.0 + 2.0 + 3.0 + 4.0 + 5.0, rel=1e-10)


def test_gpp_diario_vs_gpp_acumulado_por_ventana(conn_prueba):
    """
    Escenario real: un ciclo con SOS/EOS definido y predicciones por ventana.

    Verifica que:
      SUM(series_diarias_vpm.gpp_diario) [rango SOS..EOS]
      = predicciones_ventana.gpp_acumulado (EOS)
    """
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, t1, t2, t3, eos,
             fecha_inicio, fecha_fin, lswi_max)
        VALUES (1, 1, 'primera', '2024-04-01', '2024-05-01', '2024-05-31',
                '2024-06-30', '2024-07-31',
                '2024-03-15', '2024-07-31', 0.5)
    """)

    rng = np.random.default_rng(123)
    fechas = pd.date_range("2024-04-01", "2024-07-31", freq="D")
    valores = rng.uniform(2.0, 5.0, size=len(fechas))
    gpp_acumulado_eos = float(valores.sum())

    conn_prueba.executemany(
        """INSERT INTO series_diarias_vpm (id_parcela, fecha, gpp_diario)
           VALUES (1, ?, ?)""",
        [(f.strftime("%Y-%m-%d"), float(v)) for f, v in zip(fechas, valores)],
    )

    conn_prueba.execute("""
        INSERT INTO predicciones_ventana
            (id_ciclo, id_parcela, ventana, fecha_ventana, gpp_acumulado)
        VALUES (1, 1, 'EOS', '2024-07-31', ?)
    """, (gpp_acumulado_eos,))

    suma_bd = conn_prueba.execute(
        """SELECT SUM(gpp_diario)
           FROM series_diarias_vpm
           WHERE id_parcela = 1
             AND fecha BETWEEN '2024-04-01' AND '2024-07-31'"""
    ).fetchone()[0]
    gpp_pred = conn_prueba.execute(
        "SELECT gpp_acumulado FROM predicciones_ventana WHERE id_ciclo = 1 AND ventana = 'EOS'"
    ).fetchone()[0]

    assert suma_bd == pytest.approx(gpp_pred, rel=1e-10)
    assert suma_bd == pytest.approx(gpp_acumulado_eos, rel=1e-10)


def test_guardar_gpp_diario_mode_replace(conn_prueba):
    """
    mode='replace' elimina las filas existentes en el rango cubierto por
    los nuevos datos, las reescribe, y preserva filas fuera del rango.
    """
    fechas_viejo = pd.date_range("2024-04-01", periods=10, freq="D")
    conn_prueba.executemany(
        """INSERT INTO series_diarias_vpm (id_parcela, fecha, gpp_diario)
           VALUES (1, ?, 99.0)""",
        [(f.strftime("%Y-%m-%d"),) for f in fechas_viejo],
    )

    fechas_nuevo = pd.date_range("2024-04-05", periods=6, freq="D")
    valores = np.full(6, 3.0)
    df_gpp = pd.DataFrame({"id_1": valores}, index=fechas_nuevo)
    dfs_gpp = {"GPP": df_gpp}

    n = guardar_gpp_diario(dfs_gpp, mode="replace")
    assert n == 6

    n_filas = conn_prueba.execute(
        "SELECT COUNT(*) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()[0]
    assert n_filas == 10

    suma = conn_prueba.execute(
        "SELECT SUM(gpp_diario) FROM series_diarias_vpm WHERE id_parcela = 1"
    ).fetchone()[0]
    assert suma == pytest.approx(4 * 99.0 + 6 * 3.0, rel=1e-10)
