# tests/test_pipeline_completo.py
# from pipeline.motor_prediccion import ejecutar_pipeline_completo

# def test_replay_produce_resultado_estable(conn_prueba):
#     resultado = ejecutar_pipeline_completo(
#         id_parcela=1,
#         fecha_inicio_simulada=pd.Timestamp("2024-04-01"),
#         fecha_fin_simulada=pd.Timestamp("2024-08-01"),
#     )

#     esperado = cargar_resultado_referencia("tests/fixtures/golden_parcela_1.json")

#     pd.testing.assert_frame_equal(resultado, esperado, atol=1e-3)