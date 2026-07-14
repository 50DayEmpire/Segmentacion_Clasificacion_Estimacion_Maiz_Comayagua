# tests/test_clasificacion.py
"""
Verifica que persistir_clasificacion_v2() escribe correctamente
en predicciones_ventana y produccion_acumulada_ciclo.
"""
import pytest
import numpy as np
import pandas as pd
from pipeline.modulo_clasificacion import persistir_clasificacion_v2, _score_a_label


def test_score_a_label():
    assert _score_a_label(85.0) == "Maíz"
    assert _score_a_label(70.0) == "Maíz"
    assert _score_a_label(50.0) == "Maíz - baja probabilidad"
    assert _score_a_label(30.0) == "Maíz - baja probabilidad"
    assert _score_a_label(10.0) == "Otro"
    assert _score_a_label(0.0) == "Otro"
    assert _score_a_label(np.nan) == "Incierto"
    assert _score_a_label(None) == "Incierto"


def test_persistir_clasificacion_v2_escribe_en_predicciones_ventana(conn_prueba):
    """Tras persistir, los scores deben estar en predicciones_ventana."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)
    conn_prueba.execute("""
        INSERT INTO predicciones_ventana
            (id_ciclo, id_parcela, ventana, fecha_ventana)
        VALUES (1, 1, 'T1', '2024-05-01')
    """)

    resultado = {
        "estado": "evaluado",
        "id_parcela": 1,
        "dia_post_sos": 30,
        "patron_usado": "grano_rapido",
        "r_forma": 0.85,
        "pendiente_obs": 0.012,
        "score_compuesto": 75.0,
    }

    persistir_clasificacion_v2(conn_prueba, resultado, id_ciclo=1, ventana="T1")

    fila = conn_prueba.execute(
        "SELECT score_pearson, score_magnitud_pendiente, score_compuesto, cultivo_predicho "
        "FROM predicciones_ventana WHERE id_ciclo = 1 AND ventana = 'T1'"
    ).fetchone()

    assert fila is not None
    assert fila[0] == pytest.approx(0.85, rel=1e-6)
    assert fila[1] == pytest.approx(0.012, rel=1e-6)
    assert fila[2] == pytest.approx(75.0, rel=1e-6)
    assert fila[3] == "Maíz"


def test_persistir_clasificacion_v2_actualiza_clasificacion_final(conn_prueba):
    """Debe actualizar clasificacion_final en produccion_acumulada_ciclo."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)
    conn_prueba.execute("""
        INSERT INTO predicciones_ventana
            (id_ciclo, id_parcela, ventana, fecha_ventana)
        VALUES (1, 1, 'T1', '2024-05-01')
    """)

    resultado = {
        "estado": "evaluado",
        "id_parcela": 1,
        "dia_post_sos": 30,
        "patron_usado": "grano_rapido",
        "r_forma": 0.5,
        "pendiente_obs": 0.005,
        "score_compuesto": 40.0,
    }

    persistir_clasificacion_v2(conn_prueba, resultado, id_ciclo=1, ventana="T1")

    fila = conn_prueba.execute(
        "SELECT clasificacion_final FROM produccion_acumulada_ciclo WHERE id_ciclo = 1"
    ).fetchone()
    assert fila[0] == "Maíz - baja probabilidad"


def test_persistir_clasificacion_v2_no_evaluado_no_escribe(conn_prueba):
    """Si estado no es 'evaluado', no debe modificar nada."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)

    resultado = {"estado": "fuera_de_ventana", "motivo": "día 20 < 30"}
    persistir_clasificacion_v2(conn_prueba, resultado, id_ciclo=1, ventana="T1")

    clasif = conn_prueba.execute(
        "SELECT clasificacion_final FROM produccion_acumulada_ciclo WHERE id_ciclo = 1"
    ).fetchone()[0]
    assert clasif is None


def test_persistir_clasificacion_v2_infiere_ventana_desde_dia(conn_prueba):
    """Si no se pasa ventana, se infiere de dia_post_sos."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)
    conn_prueba.execute("""
        INSERT INTO predicciones_ventana
            (id_ciclo, id_parcela, ventana, fecha_ventana)
        VALUES (1, 1, 'T3', '2024-06-30')
    """)

    resultado = {
        "estado": "evaluado",
        "id_parcela": 1,
        "dia_post_sos": 90,
        "patron_usado": "grano_lento",
        "r_forma": 0.7,
        "pendiente_obs": 0.008,
        "score_compuesto": 60.0,
    }

    persistir_clasificacion_v2(conn_prueba, resultado, id_ciclo=1)

    fila = conn_prueba.execute(
        "SELECT ventana, score_compuesto FROM predicciones_ventana WHERE id_ciclo = 1"
    ).fetchone()
    assert fila[0] == "T3"
    assert fila[1] == pytest.approx(60.0, rel=1e-6)


def test_seed_clasificacion_clasifica_ciclo_pendiente(conn_prueba, monkeypatch):
    """seed_clasificacion debe encontrar el ciclo sin clasificación y persistir los scores."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)
    conn_prueba.execute("""
        INSERT INTO predicciones_ventana
            (id_ciclo, id_parcela, ventana, fecha_ventana)
        VALUES (1, 1, 'T1', '2024-05-01')
    """)
    conn_prueba.executescript("""
        INSERT INTO series_diarias_vpm (id_parcela, fecha, evi_crudo, lswi_crudo) VALUES
            (1, '2024-04-01', 0.15, 0.05),
            (1, '2024-04-05', 0.18, 0.06),
            (1, '2024-04-10', 0.22, 0.07),
            (1, '2024-04-15', 0.30, 0.08),
            (1, '2024-04-20', 0.35, 0.09),
            (1, '2024-04-25', 0.40, 0.10),
            (1, '2024-04-30', 0.43, 0.10),
            (1, '2024-05-05', 0.45, 0.11),
            (1, '2024-05-10', 0.48, 0.11),
            (1, '2024-05-15', 0.50, 0.12),
            (1, '2024-05-20', 0.52, 0.12),
            (1, '2024-05-25', 0.53, 0.12),
            (1, '2024-05-30', 0.54, 0.13);
    """)

    def _mock_clasificar(conn, id_parcela, sos_fecha, df_evi, fecha_evaluacion=None):
        return {
            "estado": "evaluado",
            "id_parcela": id_parcela,
            "dia_post_sos": 30,
            "patron_usado": "grano_rapido",
            "r_forma": 0.85,
            "pendiente_obs": 0.012,
            "score_compuesto": 80.0,
        }
    import pipeline.modulo_clasificacion as mc
    monkeypatch.setattr(
        "pipeline.modulo_clasificacion.clasificar_parcela_actual",
        _mock_clasificar,
    )

    from pipeline.modulo_clasificacion import seed_clasificacion
    res = seed_clasificacion(conn_prueba, temporada="primera")

    assert res["total"] == 1
    assert res["clasificados"] == 1
    assert res["fuera_ventana"] == 0
    assert res["sin_patron"] == 0
    assert res["sin_evi"] == 0

    fila = conn_prueba.execute(
        "SELECT score_compuesto, cultivo_predicho FROM predicciones_ventana WHERE id_ciclo = 1"
    ).fetchone()
    assert fila[0] == pytest.approx(80.0, rel=1e-6)
    assert fila[1] == "Maíz"

    clasif = conn_prueba.execute(
        "SELECT clasificacion_final FROM produccion_acumulada_ciclo WHERE id_ciclo = 1"
    ).fetchone()[0]
    assert clasif == "Maíz"


def test_seed_clasificacion_sin_ciclos_pendientes(conn_prueba):
    """Si todos los ciclos ya tienen clasificación, debe retornar 0."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, clasificacion_final, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'Maíz', 'activo')
    """)
    from pipeline.modulo_clasificacion import seed_clasificacion
    res = seed_clasificacion(conn_prueba, temporada="primera")
    assert res["total"] == 0
    assert res["clasificados"] == 0
    assert res["fuera_ventana"] == 0


def test_seed_clasificacion_sin_predicciones_ignora_ciclo(conn_prueba):
    """Ciclo sin predicciones no debe aparecer en los resultados."""
    conn_prueba.execute("""
        INSERT INTO produccion_acumulada_ciclo
            (id_ciclo, id_parcela, temporada, sos, estado_ciclo)
        VALUES (1, 1, 'primera', '2024-04-01', 'activo')
    """)
    from pipeline.modulo_clasificacion import seed_clasificacion
    res = seed_clasificacion(conn_prueba, temporada="primera")
    assert res["total"] == 0
    assert res["clasificados"] == 0
    assert res["fuera_ventana"] == 0
