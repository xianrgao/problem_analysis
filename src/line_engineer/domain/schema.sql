-- DEMONSTRATION DATA ONLY. Times are ISO 8601 local Asia/Shanghai timestamps.
-- One workpiece can visit multiple stations; measurements and NCs are events.
-- Always use COUNT(DISTINCT tg) for workpiece counts, not NC event counts.
PRAGMA foreign_keys = ON;
CREATE TABLE workpieces (
    tg TEXT PRIMARY KEY,
    model TEXT NOT NULL,
    launched_at TEXT NOT NULL
);
CREATE TABLE stations (
    station_id TEXT PRIMARY KEY,
    area TEXT NOT NULL,
    name TEXT NOT NULL,
    sequence INTEGER NOT NULL
);
CREATE TABLE visits (
    visit_id INTEGER PRIMARY KEY,
    tg TEXT NOT NULL REFERENCES workpieces(tg),
    station_id TEXT NOT NULL REFERENCES stations(station_id),
    entered_at TEXT NOT NULL,
    exited_at TEXT NOT NULL
);
CREATE TABLE measurements (
    measurement_id INTEGER PRIMARY KEY,
    visit_id INTEGER NOT NULL REFERENCES visits(visit_id),
    task TEXT NOT NULL,
    parameter TEXT NOT NULL,
    value REAL NOT NULL,
    unit TEXT NOT NULL,
    lower_limit REAL NOT NULL,
    upper_limit REAL NOT NULL,
    result TEXT NOT NULL CHECK (result IN ('OK','NC'))
);
CREATE TABLE nc_events (
    nc_id INTEGER PRIMARY KEY,
    measurement_id INTEGER NOT NULL REFERENCES measurements(measurement_id),
    code TEXT NOT NULL,
    occurred_at TEXT NOT NULL
);
CREATE INDEX ix_visits_tg ON visits(tg);
CREATE INDEX ix_visits_station_time ON visits(station_id, entered_at);
CREATE INDEX ix_measurements_visit ON measurements(visit_id);
CREATE INDEX ix_nc_measurement ON nc_events(measurement_id);
CREATE INDEX ix_nc_time ON nc_events(occurred_at);
