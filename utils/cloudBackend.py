import openeo

def ejecutar_batch_indices(connection: openeo.Connection, data_cube: openeo.DataCube):
    """
    Ejecuta un batch de índices en un DataCube de entrada.

    Args:
        connection: Conexión a openEO.
        data_cube: DataCube de entrada.
        batch_config: Diccionario con la configuración del batch.

    Returns:
        DataCube resultante después de aplicar los índices.
    """
    dfs_vpm_crudos.save_result(format="JSON")
    job = dfs_vpm_crudos.create_job(title="Prueba asincrona evi lswi")
    job.start_and_wait()
    serie = job.get_results().get_asset().load_json()
    dfs_vpm_crudos = openeo_dict_to_dataframes(serie, nombres_bandas=["EVI", "LSWI"])
    return dfs_vpm_crudos
