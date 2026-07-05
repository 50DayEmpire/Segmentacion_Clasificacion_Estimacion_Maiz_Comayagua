CREATE TABLE IF NOT EXISTS series_diarias_vpm (
    id_parcela                  INTEGER NOT NULL,
    fecha                       DATE    NOT NULL,
    evi_crudo                   REAL,
    lswi_crudo                  REAL,
    temperatura_diaria_promedio REAL,
    radiacion_total_promedio    REAL,
    gpp_diario                  REAL,
    PRIMARY KEY (id_parcela, fecha),
    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
);

CREATE TABLE IF NOT EXISTS produccion_acumulada_ciclo (
    id_ciclo         INTEGER NOT NULL,
    id_parcela       INTEGER NOT NULL,
    temporada        TEXT,
    lswi_max         REAL,
    sos              DATE,
    t1               DATE,
    t2               DATE,
    t3               DATE,
    eos              DATE,
    fecha_inicio     DATE,
    fecha_fin        DATE,
    rendimiento      REAL,
    produccion_total REAL,
    PRIMARY KEY (id_ciclo),
    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela),
    UNIQUE (id_parcela, fecha_inicio, fecha_fin)
);

CREATE TABLE IF NOT EXISTS indices_suavizados (
    id_ciclo   INTEGER NOT NULL,
    fecha      DATE    NOT NULL,
    id_parcela INTEGER NOT NULL,
    evi        REAL,
    lswi       REAL,
    PRIMARY KEY (id_ciclo, fecha),
    FOREIGN KEY (id_ciclo) REFERENCES produccion_acumulada_ciclo(id_ciclo),
    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
);

CREATE TABLE IF NOT EXISTS predicciones_ventana (
    id_prediccion              INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    id_ciclo                   INTEGER NOT NULL,
    id_parcela                 INTEGER NOT NULL,
    ventana                    TEXT    NOT NULL,
    fecha_ventana              DATE    NOT NULL,
    lswi_max_efectivo_usado    REAL,
    gpp_acumulado              REAL,
    npp_acumulado              REAL,
    rendimiento_estimado_qq_ha       REAL,
    rendimiento_estimado_qq_parcela  REAL,
    fecha_congelamiento        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (id_ciclo, ventana),
    CHECK (ventana IN ('T1', 'T2', 'T3')),
    FOREIGN KEY (id_ciclo) REFERENCES produccion_acumulada_ciclo(id_ciclo),
    FOREIGN KEY (id_parcela) REFERENCES parcelas_vigentes(id_parcela)
);

CREATE TABLE IF NOT EXISTS climatologia_diaria (
    id_region           INTEGER NOT NULL DEFAULT 1,
    variable            TEXT    NOT NULL,
    dia_anio            INTEGER NOT NULL,
    valor_climatologico REAL    NOT NULL,
    anio_min_incluido   INTEGER NOT NULL,
    anio_max_incluido   INTEGER NOT NULL,
    fecha_calculo       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id_region, variable, dia_anio),
    CHECK (variable IN ('PAR', 'temperatura')),
    CHECK (dia_anio BETWEEN 1 AND 366)
);

CREATE TABLE IF NOT EXISTS series_extrapoladas_ventana (
    id_prediccion    INTEGER NOT NULL,
    fecha            DATE    NOT NULL,
    evi_extrapolado  REAL,
    lswi_extrapolado REAL,
    PRIMARY KEY (id_prediccion, fecha),
    FOREIGN KEY (id_prediccion) REFERENCES predicciones_ventana(id_prediccion)
        ON DELETE CASCADE
);
