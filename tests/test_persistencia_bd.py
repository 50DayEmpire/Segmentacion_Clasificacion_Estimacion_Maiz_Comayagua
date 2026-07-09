# tests/test_persistencia_bd.py
import pandas as pd
from pipeline.ingesta import guardar_indices_crudos


def test_upsert_no_borra_gpp_existente(conn_prueba):
    conn_prueba.execute(
        "INSERT INTO parcelas_vigentes (id_parcela) VALUES (100)"
    )
    conn_prueba.execute(
        "INSERT INTO series_diarias_vpm (id_parcela, fecha, evi_crudo, gpp_diario) "
        "VALUES (100, '2024-05-01', 0.4, 12.3)"
    )
    conn_prueba.commit()

    dfs = {
        "EVI":  pd.DataFrame({"id_1": [0.45]}, index=pd.to_datetime(["2024-05-01"])),
        "LSWI": pd.DataFrame({"id_1": [0.10]}, index=pd.to_datetime(["2024-05-01"])),
    }

    guardar_indices_crudos(dfs, mode="append")

    fila = conn_prueba.execute(
        "SELECT evi_crudo, gpp_diario FROM series_diarias_vpm "
        "WHERE id_parcela=100 AND fecha='2024-05-01'"
    ).fetchone()

    # evi_crudo NO debe sobrescribirse (ya tenía valor real: 0.4, no 0.45)
    assert fila[0] == 0.4
    # gpp_diario NO debe tocarse por esta función en absoluto
    assert fila[1] == 12.3