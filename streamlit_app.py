import streamlit as st
import pandas as pd
import sqlite3
import math
import io
from dataclasses import dataclass, field
from contextlib import contextmanager

st.set_page_config(
    page_title="ElecCalc UNE-HD 60364",
    page_icon="⚡",
    layout="wide"
)

# ── CONSTANTES ────────────────────────────────────────────────
SQRT3         = math.sqrt(3)
TEMP_COEFF_CU = 0.00393
TEMP_COEFF_AL = 0.00403
DB_PATH       = "eleccalc.db"

PROTECTION_RATINGS = {
    "MCB":  [6,10,13,16,20,25,32,40,50,63],
    "MCCB": [16,20,25,32,40,50,63,80,100,125,
             160,200,250,315,400,500,630],
    "FUSE": [2,4,6,10,16,20,25,32,40,50,
             63,80,100,125,160,200,250,315,400],
}
VDROP_LIMITS = {
    "MOTOR":5.0, "LIGHTING":3.0,
    "HEATING":3.0, "MIXED":3.0
}
BREAKING_CAPACITY = {
    "MCB":6.0, "MCCB":25.0, "FUSE":80.0
}

# Secciones comerciales normalizadas
STANDARD_SECTIONS = [
    1.5, 2.5, 4.0, 6.0, 10.0, 16.0, 25.0,
    35.0, 50.0, 70.0, 95.0, 120.0, 150.0,
    185.0, 240.0, 300.0
]

# ── DATACLASSES ───────────────────────────────────────────────
@dataclass
class CircuitInput:
    circuit_ref:          str
    description:          str   = ""
    circuit_type:         str   = "THREE_PHASE"
    load_type:            str   = "MOTOR"
    power_kw:             float = 0.0
    power_factor:         float = 0.85
    efficiency:           float = 0.92
    demand_factor:        float = 1.0
    simultaneity_factor:  float = 1.0
    voltage_v:            float = 400.0
    conductor_material:   str   = "Cu"
    insulation_type:      str   = "XLPE"
    installation_method:  str   = "B1"
    length_m:             float = 10.0
    ambient_temp_c:       int   = 40
    num_grouped_circuits: int   = 1
    grouping_arrangement: str   = "touching"
    icc_origin_ka:        float = 10.0
    protection_type:      str   = "MCB"
    protection_curve:     str   = "C"
    vdrop_limit_pct:      float = 5.0  # límite ΔU% configurable

@dataclass
class CircuitOutput:
    # Corrientes
    ib_amperes:             float = 0.0
    kt:                     float = 1.0
    kg:                     float = 1.0
    total_correction:       float = 1.0
    ib_corrected:           float = 0.0

    # Sección por CAPACIDAD TÉRMICA
    section_thermal_mm2:    float = 0.0
    iz_thermal_amperes:     float = 0.0
    iz_thermal_corrected:   float = 0.0

    # Sección por CAÍDA DE TENSIÓN
    section_vdrop_mm2:      float = 0.0

    # Sección FINAL (máximo de ambas)
    section_phase_mm2:      float = 0.0
    section_neutral_mm2:    float = 0.0
    section_pe_mm2:         float = 0.0

    # Iz FINAL con sección definitiva
    iz_amperes:             float = 0.0
    iz_corrected:           float = 0.0

    # Protección
    in_protection_amperes:  float = 0.0

    # Caída de tensión FINAL
    resistivity_ohm_km:     float = 0.0
    reactance_ohm_km:       float = 0.0
    voltage_drop_v:         float = 0.0
    voltage_drop_pct:       float = 0.0
    vdrop_limit_pct:        float = 5.0

    # Cortocircuito
    icc_max_origin_ka:      float = 0.0
    icc_end_ka:             float = 0.0
    icc_min_ka:             float = 0.0
    thermal_icc_time_s:     float = 0.0

    # Verificaciones individuales
    check_ib_lt_in:         bool  = False
    check_in_lt_iz:         bool  = False
    check_vdrop_ok:         bool  = False
    check_icc_breaking:     bool  = False
    check_thermal_icc:      bool  = False
    overall_ok:             bool  = False

    # Diagnóstico
    section_limited_by:     str   = ""  # "THERMAL" o "VDROP"
    warnings:               list  = field(default_factory=list)
    errors:                 list  = field(default_factory=list)
    iterations:             int   = 0

    @property
    def status(self):
        if self.errors:      return "ERROR"
        if self.overall_ok:  return "OK"
        return "NOT OK"

# ── BASE DE DATOS ─────────────────────────────────────────────
@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

@st.cache_resource
def init_db():
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS current_capacity_cu (
            installation_method TEXT,
            insulation_type     TEXT,
            num_conductors      INTEGER,
            section_mm2         REAL,
            imax_amperes        REAL,
            UNIQUE(installation_method,insulation_type,
                   num_conductors,section_mm2)
        );
        CREATE TABLE IF NOT EXISTS correction_temp (
            insulation_type TEXT,
            ambient_temp_c  INTEGER,
            factor          REAL,
            UNIQUE(insulation_type,ambient_temp_c)
        );
        CREATE TABLE IF NOT EXISTS correction_group (
            num_circuits INTEGER,
            arrangement  TEXT,
            factor       REAL,
            UNIQUE(num_circuits,arrangement)
        );
        CREATE TABLE IF NOT EXISTS conductor_impedance (
            material    TEXT,
            section_mm2 REAL,
            r_ohm_km    REAL,
            x_ohm_km    REAL,
            UNIQUE(material,section_mm2)
        );
        """)

        capacity_data = [
            # A1 PVC
            ("A1","PVC",2,1.5,13.5),("A1","PVC",2,2.5,18.0),
            ("A1","PVC",2,4.0,24.0),("A1","PVC",2,6.0,31.0),
            ("A1","PVC",2,10.0,42.0),("A1","PVC",2,16.0,56.0),
            ("A1","PVC",2,25.0,73.0),("A1","PVC",2,35.0,89.0),
            ("A1","PVC",2,50.0,108.0),("A1","PVC",2,70.0,136.0),
            ("A1","PVC",2,95.0,164.0),("A1","PVC",2,120.0,188.0),
            ("A1","PVC",3,1.5,13.0),("A1","PVC",3,2.5,17.5),
            ("A1","PVC",3,4.0,23.0),("A1","PVC",3,6.0,29.0),
            ("A1","PVC",3,10.0,39.0),("A1","PVC",3,16.0,52.0),
            ("A1","PVC",3,25.0,68.0),("A1","PVC",3,35.0,83.0),
            ("A1","PVC",3,50.0,99.0),("A1","PVC",3,70.0,125.0),
            ("A1","PVC",3,95.0,150.0),("A1","PVC",3,120.0,172.0),
            # A1 XLPE
            ("A1","XLPE",2,1.5,17.5),("A1","XLPE",2,2.5,24.0),
            ("A1","XLPE",2,4.0,32.0),("A1","XLPE",2,6.0,41.0),
            ("A1","XLPE",2,10.0,57.0),("A1","XLPE",2,16.0,76.0),
            ("A1","XLPE",2,25.0,101.0),("A1","XLPE",2,35.0,125.0),
            ("A1","XLPE",2,50.0,151.0),("A1","XLPE",2,70.0,192.0),
            ("A1","XLPE",2,95.0,232.0),("A1","XLPE",2,120.0,269.0),
            ("A1","XLPE",3,1.5,15.5),("A1","XLPE",3,2.5,21.0),
            ("A1","XLPE",3,4.0,28.0),("A1","XLPE",3,6.0,36.0),
            ("A1","XLPE",3,10.0,50.0),("A1","XLPE",3,16.0,68.0),
            ("A1","XLPE",3,25.0,89.0),("A1","XLPE",3,35.0,110.0),
            ("A1","XLPE",3,50.0,134.0),("A1","XLPE",3,70.0,171.0),
            ("A1","XLPE",3,95.0,207.0),("A1","XLPE",3,120.0,239.0),
            # B1 PVC
            ("B1","PVC",2,1.5,15.5),("B1","PVC",2,2.5,21.0),
            ("B1","PVC",2,4.0,28.0),("B1","PVC",2,6.0,36.0),
            ("B1","PVC",2,10.0,50.0),("B1","PVC",2,16.0,68.0),
            ("B1","PVC",2,25.0,89.0),("B1","PVC",2,35.0,110.0),
            ("B1","PVC",2,50.0,134.0),("B1","PVC",2,70.0,171.0),
            ("B1","PVC",2,95.0,207.0),("B1","PVC",2,120.0,239.0),
            ("B1","PVC",2,150.0,275.0),("B1","PVC",2,185.0,314.0),
            ("B1","PVC",2,240.0,370.0),
            ("B1","PVC",3,1.5,13.5),("B1","PVC",3,2.5,18.0),
            ("B1","PVC",3,4.0,24.0),("B1","PVC",3,6.0,31.0),
            ("B1","PVC",3,10.0,42.0),("B1","PVC",3,16.0,56.0),
            ("B1","PVC",3,25.0,73.0),("B1","PVC",3,35.0,89.0),
            ("B1","PVC",3,50.0,108.0),("B1","PVC",3,70.0,136.0),
            ("B1","PVC",3,95.0,164.0),("B1","PVC",3,120.0,188.0),
            ("B1","PVC",3,150.0,216.0),("B1","PVC",3,185.0,245.0),
            ("B1","PVC",3,240.0,286.0),
            # B1 XLPE
            ("B1","XLPE",2,1.5,19.5),("B1","XLPE",2,2.5,27.0),
            ("B1","XLPE",2,4.0,36.0),("B1","XLPE",2,6.0,46.0),
            ("B1","XLPE",2,10.0,63.0),("B1","XLPE",2,16.0,85.0),
            ("B1","XLPE",2,25.0,112.0),("B1","XLPE",2,35.0,138.0),
            ("B1","XLPE",2,50.0,168.0),("B1","XLPE",2,70.0,213.0),
            ("B1","XLPE",2,95.0,258.0),("B1","XLPE",2,120.0,299.0),
            ("B1","XLPE",2,150.0,344.0),("B1","XLPE",2,185.0,392.0),
            ("B1","XLPE",2,240.0,461.0),
            ("B1","XLPE",3,1.5,17.0),("B1","XLPE",3,2.5,23.0),
            ("B1","XLPE",3,4.0,31.0),("B1","XLPE",3,6.0,40.0),
            ("B1","XLPE",3,10.0,54.0),("B1","XLPE",3,16.0,73.0),
            ("B1","XLPE",3,25.0,95.0),("B1","XLPE",3,35.0,117.0),
            ("B1","XLPE",3,50.0,141.0),("B1","XLPE",3,70.0,179.0),
            ("B1","XLPE",3,95.0,216.0),("B1","XLPE",3,120.0,249.0),
            ("B1","XLPE",3,150.0,285.0),("B1","XLPE",3,185.0,324.0),
            ("B1","XLPE",3,240.0,380.0),
            # C PVC
            ("C","PVC",2,1.5,17.5),("C","PVC",2,2.5,24.0),
            ("C","PVC",2,4.0,32.0),("C","PVC",2,6.0,41.0),
            ("C","PVC",2,10.0,57.0),("C","PVC",2,16.0,76.0),
            ("C","PVC",2,25.0,101.0),("C","PVC",2,35.0,125.0),
            ("C","PVC",2,50.0,151.0),("C","PVC",2,70.0,192.0),
            ("C","PVC",2,95.0,232.0),("C","PVC",2,120.0,269.0),
            ("C","PVC",2,150.0,309.0),("C","PVC",2,185.0,353.0),
            ("C","PVC",2,240.0,415.0),
            ("C","PVC",3,1.5,15.5),("C","PVC",3,2.5,21.0),
            ("C","PVC",3,4.0,28.0),("C","PVC",3,6.0,36.0),
            ("C","PVC",3,10.0,50.0),("C","PVC",3,16.0,68.0),
            ("C","PVC",3,25.0,89.0),("C","PVC",3,35.0,110.0),
            ("C","PVC",3,50.0,134.0),("C","PVC",3,70.0,171.0),
            ("C","PVC",3,95.0,207.0),("C","PVC",3,120.0,239.0),
            ("C","PVC",3,150.0,275.0),("C","PVC",3,185.0,314.0),
            ("C","PVC",3,240.0,370.0),
            # C XLPE
            ("C","XLPE",2,1.5,22.0),("C","XLPE",2,2.5,30.0),
            ("C","XLPE",2,4.0,40.0),("C","XLPE",2,6.0,52.0),
            ("C","XLPE",2,10.0,71.0),("C","XLPE",2,16.0,96.0),
            ("C","XLPE",2,25.0,127.0),("C","XLPE",2,35.0,157.0),
            ("C","XLPE",2,50.0,192.0),("C","XLPE",2,70.0,245.0),
            ("C","XLPE",2,95.0,298.0),("C","XLPE",2,120.0,346.0),
            ("C","XLPE",2,150.0,399.0),("C","XLPE",2,185.0,456.0),
            ("C","XLPE",2,240.0,538.0),
            ("C","XLPE",3,1.5,19.5),("C","XLPE",3,2.5,27.0),
            ("C","XLPE",3,4.0,36.0),("C","XLPE",3,6.0,46.0),
            ("C","XLPE",3,10.0,63.0),("C","XLPE",3,16.0,85.0),
            ("C","XLPE",3,25.0,112.0),("C","XLPE",3,35.0,138.0),
            ("C","XLPE",3,50.0,168.0),("C","XLPE",3,70.0,213.0),
            ("C","XLPE",3,95.0,258.0),("C","XLPE",3,120.0,299.0),
            ("C","XLPE",3,150.0,344.0),("C","XLPE",3,185.0,392.0),
            ("C","XLPE",3,240.0,461.0),
            # E XLPE
            ("E","XLPE",2,1.5,24.0),("E","XLPE",2,2.5,33.0),
            ("E","XLPE",2,4.0,45.0),("E","XLPE",2,6.0,58.0),
            ("E","XLPE",2,10.0,80.0),("E","XLPE",2,16.0,107.0),
            ("E","XLPE",2,25.0,142.0),("E","XLPE",2,35.0,175.0),
            ("E","XLPE",2,50.0,214.0),("E","XLPE",2,70.0,273.0),
            ("E","XLPE",2,95.0,332.0),("E","XLPE",2,120.0,386.0),
            ("E","XLPE",2,150.0,445.0),("E","XLPE",2,185.0,510.0),
            ("E","XLPE",2,240.0,607.0),
            ("E","XLPE",3,1.5,22.0),("E","XLPE",3,2.5,30.0),
            ("E","XLPE",3,4.0,40.0),("E","XLPE",3,6.0,52.0),
            ("E","XLPE",3,10.0,71.0),("E","XLPE",3,16.0,96.0),
            ("E","XLPE",3,25.0,127.0),("E","XLPE",3,35.0,157.0),
            ("E","XLPE",3,50.0,192.0),("E","XLPE",3,70.0,245.0),
            ("E","XLPE",3,95.0,298.0),("E","XLPE",3,120.0,346.0),
            ("E","XLPE",3,150.0,399.0),("E","XLPE",3,185.0,456.0),
            ("E","XLPE",3,240.0,538.0),
            # E PVC
            ("E","PVC",2,1.5,19.5),("E","PVC",2,2.5,27.0),
            ("E","PVC",2,4.0,36.0),("E","PVC",2,6.0,46.0),
            ("E","PVC",2,10.0,63.0),("E","PVC",2,16.0,85.0),
            ("E","PVC",2,25.0,112.0),("E","PVC",2,35.0,138.0),
            ("E","PVC",2,50.0,168.0),("E","PVC",2,70.0,213.0),
            ("E","PVC",2,95.0,258.0),("E","PVC",2,120.0,299.0),
            ("E","PVC",3,1.5,17.5),("E","PVC",3,2.5,24.0),
            ("E","PVC",3,4.0,32.0),("E","PVC",3,6.0,41.0),
            ("E","PVC",3,10.0,57.0),("E","PVC",3,16.0,76.0),
            ("E","PVC",3,25.0,101.0),("E","PVC",3,35.0,125.0),
            ("E","PVC",3,50.0,151.0),("E","PVC",3,70.0,192.0),
            ("E","PVC",3,95.0,232.0),("E","PVC",3,120.0,269.0),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO current_capacity_cu "
            "(installation_method,insulation_type,"
            "num_conductors,section_mm2,imax_amperes) "
            "VALUES(?,?,?,?,?)", capacity_data
        )

        temp_data = [
            ("PVC",10,1.22),("PVC",15,1.17),("PVC",20,1.12),
            ("PVC",25,1.06),("PVC",30,1.00),("PVC",35,0.94),
            ("PVC",40,0.87),("PVC",45,0.79),("PVC",50,0.71),
            ("PVC",55,0.61),("PVC",60,0.50),
            ("XLPE",10,1.15),("XLPE",15,1.12),("XLPE",20,1.08),
            ("XLPE",25,1.04),("XLPE",30,1.00),("XLPE",35,0.96),
            ("XLPE",40,0.91),("XLPE",45,0.87),("XLPE",50,0.82),
            ("XLPE",55,0.76),("XLPE",60,0.71),("XLPE",65,0.65),
            ("XLPE",70,0.58),("XLPE",75,0.50),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO correction_temp "
            "(insulation_type,ambient_temp_c,factor) "
            "VALUES(?,?,?)", temp_data
        )

        group_data = [
            (1,"touching",1.00),(2,"touching",0.80),
            (3,"touching",0.70),(4,"touching",0.65),
            (5,"touching",0.60),(6,"touching",0.57),
            (7,"touching",0.54),(8,"touching",0.52),
            (9,"touching",0.50),(12,"touching",0.45),
            (16,"touching",0.41),(20,"touching",0.38),
            (1,"spaced",1.00),(2,"spaced",0.88),
            (3,"spaced",0.82),(4,"spaced",0.77),
            (5,"spaced",0.75),(6,"spaced",0.73),
            (9,"spaced",0.72),(12,"spaced",0.72),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO correction_group "
            "(num_circuits,arrangement,factor) "
            "VALUES(?,?,?)", group_data
        )

        impedance_data = [
            ("Cu",1.5,12.10,0.115),("Cu",2.5,7.41,0.110),
            ("Cu",4.0,4.61,0.107),("Cu",6.0,3.08,0.102),
            ("Cu",10.0,1.83,0.096),("Cu",16.0,1.15,0.090),
            ("Cu",25.0,0.727,0.086),("Cu",35.0,0.524,0.083),
            ("Cu",50.0,0.387,0.080),("Cu",70.0,0.268,0.077),
            ("Cu",95.0,0.193,0.075),("Cu",120.0,0.153,0.074),
            ("Cu",150.0,0.124,0.073),("Cu",185.0,0.0991,0.072),
            ("Cu",240.0,0.0754,0.071),("Cu",300.0,0.0601,0.070),
            ("Al",16.0,1.91,0.090),("Al",25.0,1.20,0.086),
            ("Al",35.0,0.868,0.083),("Al",50.0,0.641,0.080),
            ("Al",70.0,0.443,0.077),("Al",95.0,0.320,0.075),
            ("Al",120.0,0.253,0.074),("Al",150.0,0.206,0.073),
            ("Al",185.0,0.164,0.072),("Al",240.0,0.125,0.071),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO conductor_impedance "
            "(material,section_mm2,r_ohm_km,x_ohm_km) "
            "VALUES(?,?,?,?)", impedance_data
        )
    return True

# ── FUNCIONES AUXILIARES ──────────────────────────────────────
def get_sections(method, insulation, n_cond):
    with get_db() as conn:
        rows = conn.execute(
            "SELECT section_mm2, imax_amperes "
            "FROM current_capacity_cu "
            "WHERE installation_method=? "
            "AND insulation_type=? AND num_conductors=? "
            "ORDER BY section_mm2 ASC",
            (method, insulation, n_cond)
        ).fetchall()
    return [(r["section_mm2"], r["imax_amperes"]) for r in rows]

def get_kt(insulation, temp):
    with get_db() as conn:
        row = conn.execute(
            "SELECT factor FROM correction_temp "
            "WHERE insulation_type=? AND ambient_temp_c=?",
            (insulation, temp)
        ).fetchone()
        if row:
            return row["factor"]
        rows = conn.execute(
            "SELECT ambient_temp_c, factor "
            "FROM correction_temp "
            "WHERE insulation_type=? "
            "ORDER BY ambient_temp_c",
            (insulation,)
        ).fetchall()
    pts = [(r["ambient_temp_c"], r["factor"]) for r in rows]
    for i in range(len(pts)-1):
        t0,f0 = pts[i]; t1,f1 = pts[i+1]
        if t0 <= temp <= t1:
            return round(f0+(f1-f0)*(temp-t0)/(t1-t0),4)
    return pts[-1][1] if temp > pts[-1][0] else pts[0][1]

def get_kg(n, arr):
    with get_db() as conn:
        row = conn.execute(
            "SELECT factor FROM correction_group "
            "WHERE num_circuits=? AND arrangement=?",
            (n, arr)
        ).fetchone()
        if row:
            return row["factor"]
        row = conn.execute(
            "SELECT factor FROM correction_group "
            "WHERE num_circuits<=? AND arrangement=? "
            "ORDER BY num_circuits DESC LIMIT 1",
            (n, arr)
        ).fetchone()
    return row["factor"] if row else 1.0

def get_impedance(material, section):
    with get_db() as conn:
        row = conn.execute(
            "SELECT r_ohm_km, x_ohm_km "
            "FROM conductor_impedance "
            "WHERE material=? AND section_mm2=?",
            (material, section)
        ).fetchone()
    if row:
        return row["r_ohm_km"], row["x_ohm_km"]
    rho = 0.01724 if material == "Cu" else 0.02830
    return (rho/section)*1000, 0.08

def normalize_section(s, material="Cu"):
    std = STANDARD_SECTIONS.copy()
    if material == "Al":
        std = [x for x in std if x >= 16]
    return next((x for x in std if x >= s), std[-1])

def calc_vdrop(ib, length_m, section, material,
               insulation, circuit_type, power_factor):
    """
    Calcula caída de tensión para una sección dada.
    Retorna (delta_u_v, delta_u_pct, r_km, x_km)
    """
    r_km, x_km = get_impedance(material, section)
    t_max = 70.0 if insulation == "PVC" else 90.0
    alpha = TEMP_COEFF_CU if material == "Cu" else TEMP_COEFF_AL
    r_km_t = r_km * (1 + alpha * (t_max - 20))
    r_m    = r_km_t / 1000.0
    x_m    = x_km  / 1000.0
    cosp   = power_factor
    sinp   = math.sqrt(max(0, 1 - cosp**2))
    fac    = SQRT3 if circuit_type == "THREE_PHASE" else 2.0

    if section < 50:
        du = fac * ib * length_m * r_m
    else:
        du = fac * ib * length_m * (r_m*cosp + x_m*sinp)

    return du, r_km_t, x_km

def find_section_for_vdrop(ib, length_m, voltage_v,
                            material, insulation,
                            circuit_type, power_factor,
                            vdrop_limit_pct):
    """
    Calcula la sección mínima para que ΔU% ≤ límite.
    Itera por todas las secciones normalizadas de menor a mayor.
    """
    std = STANDARD_SECTIONS.copy()
    if material == "Al":
        std = [x for x in std if x >= 16]

    for s in std:
        du, _, _ = calc_vdrop(
            ib, length_m, s, material,
            insulation, circuit_type, power_factor
        )
        du_pct = (du / voltage_v) * 100
        if du_pct <= vdrop_limit_pct:
            return s

    return std[-1]  # máxima disponible

def calc_icc(voltage_v, icc_origin_ka,
             section, length_m,
             material, circuit_type):
    """Corriente de cortocircuito en fin de línea"""
    if circuit_type == "THREE_PHASE":
        z_o = voltage_v / (SQRT3 * icc_origin_ka * 1000)
    else:
        z_o = voltage_v / (2 * icc_origin_ka * 1000)

    r0, x0 = get_impedance(material, section)
    fz = 1 if circuit_type == "THREE_PHASE" else 2
    zr = z_o + fz * (r0/1000) * length_m
    zx = fz * (x0/1000) * length_m
    zt = math.sqrt(zr**2 + zx**2)

    if circuit_type == "THREE_PHASE":
        return round((voltage_v / (SQRT3 * zt)) / 1000, 4)
    else:
        return round((voltage_v / (2 * zt)) / 1000, 4)

def calc_icc_min(voltage_v, icc_origin_ka,
                 section_phase, section_pe,
                 length_m, material,
                 insulation, circuit_type):
    """
    Icc mínima: fallo fase-PE en fin de línea
    Usado para verificar disparo de protección
    """
    u_phase = (voltage_v / SQRT3
               if circuit_type == "THREE_PHASE"
               else voltage_v)

    t_max = 70.0 if insulation == "PVC" else 90.0
    alpha = (TEMP_COEFF_CU if material == "Cu"
             else TEMP_COEFF_AL)
    corr  = 1 + alpha * (t_max - 20)

    r_ph, _ = get_impedance(material, section_phase)
    r_pe, _ = get_impedance(material, section_pe)

    r_ph_m = (r_ph / 1000) * corr
    r_pe_m = (r_pe / 1000) * corr

    z_loop = (r_ph_m + r_pe_m) * length_m
    if z_loop <= 0:
        return 0.0
    return round((u_phase / z_loop) / 1000, 4)

def calc_thermal_icc_time(section, icc_ka, material):
    """
    Tiempo máximo admisible de cortocircuito (s)
    Fórmula: t = (k·S / Icc)²
    k = 115 (Cu-PVC), 143 (Cu-XLPE), 74 (Al-PVC), 94 (Al-XLPE)
    """
    k = 143 if material == "Cu" else 94
    if icc_ka <= 0:
        return 999.0
    return round((k * section / (icc_ka * 1000))**2, 4)

# ── MOTOR DE CÁLCULO CON ITERACIÓN ───────────────────────────
def calculate(inp: CircuitInput) -> CircuitOutput:
    """
    Algoritmo iterativo:
    1. Calcula Ib
    2. Busca S_térmica (capacidad de corriente)
    3. Busca S_caída (caída de tensión)
    4. S_final = MAX(S_térmica, S_caída)
    5. Recalcula Iz, ΔU, Icc con S_final
    6. Verifica todos los checks
    7. Si algún check falla por sección → sube sección e itera
    """
    out = CircuitOutput()
    out.vdrop_limit_pct = inp.vdrop_limit_pct

    # ── PASO 1: Corriente de diseño ───────────────────────────
    p_w   = (inp.power_kw * 1000
             * inp.demand_factor
             * inp.simultaneity_factor)
    denom = inp.power_factor * inp.efficiency

    if denom <= 0:
        out.errors.append("cosφ·η debe ser > 0")
        return out

    if inp.circuit_type == "THREE_PHASE":
        ib = p_w / (SQRT3 * inp.voltage_v * denom)
    else:
        ib = p_w / (inp.voltage_v * denom)

    if inp.load_type == "MOTOR":
        ib *= 1.25
        out.warnings.append("Motor: Ib×1.25 (REBT ITC-BT-47)")

    out.ib_amperes = round(ib, 4)

    # ── PASO 2: Factores de corrección ────────────────────────
    out.kt = get_kt(inp.insulation_type, inp.ambient_temp_c)
    out.kg = get_kg(
        inp.num_grouped_circuits, inp.grouping_arrangement
    )
    out.total_correction = round(out.kt * out.kg, 4)

    if out.total_correction <= 0:
        out.errors.append("Factor de corrección total = 0")
        return out

    out.ib_corrected = round(
        out.ib_amperes / out.total_correction, 4
    )

    n_cond = 2 if inp.circuit_type == "SINGLE_PHASE" else 3

    # ── PASO 3: Sección térmica ───────────────────────────────
    sections = get_sections(
        inp.installation_method,
        inp.insulation_type,
        n_cond
    )
    if not sections:
        out.errors.append(
            f"Sin datos en tabla para "
            f"{inp.installation_method}/"
            f"{inp.insulation_type}/{n_cond} cond."
        )
        return out

    found_thermal = next(
        ((s, iz) for s, iz in sections
         if iz >= out.ib_corrected), None
    )
    if found_thermal is None:
        out.errors.append(
            f"Ninguna sección soporta "
            f"Ib'={out.ib_corrected:.1f}A"
        )
        return out

    out.section_thermal_mm2  = found_thermal[0]
    out.iz_thermal_amperes   = found_thermal[1]
    out.iz_thermal_corrected = round(
        out.iz_thermal_amperes * out.total_correction, 2
    )

    # ── PASO 4: Sección por caída de tensión ──────────────────
    out.section_vdrop_mm2 = find_section_for_vdrop(
        ib           = out.ib_amperes,
        length_m     = inp.length_m,
        voltage_v    = inp.voltage_v,
        material     = inp.conductor_material,
        insulation   = inp.insulation_type,
        circuit_type = inp.circuit_type,
        power_factor = inp.power_factor,
        vdrop_limit_pct = inp.vdrop_limit_pct
    )

    # ── PASO 5: Sección final = MAX(térmica, caída tensión) ───
    s_min_rebt = 1.5 if inp.load_type == "LIGHTING" else 2.5
    s_final = max(
        out.section_thermal_mm2,
        out.section_vdrop_mm2,
        s_min_rebt
    )

    # Determinar qué criterio limitó
    if s_final == out.section_vdrop_mm2 and \
       out.section_vdrop_mm2 > out.section_thermal_mm2:
        out.section_limited_by = "CAÍDA DE TENSIÓN"
        out.warnings.append(
            f"⚡ Sección limitada por ΔU%: "
            f"S_térmica={out.section_thermal_mm2}mm² → "
            f"S_ΔU={out.section_vdrop_mm2}mm²"
        )
    else:
        out.section_limited_by = "CAPACIDAD TÉRMICA"

    out.section_phase_mm2 = s_final

    # ── PASO 6: Iteración hasta que todos los checks pasen ────
    MAX_ITER = len(STANDARD_SECTIONS)
    std_sections = [s for s in STANDARD_SECTIONS
                    if s >= s_final]

    for iteration in range(MAX_ITER):
        out.iterations = iteration + 1
        s = out.section_phase_mm2

        # Iz real con la sección actual
        iz_found = next(
            (iz for sec, iz in sections if sec == s), None
        )
        if iz_found is None:
            # Buscar la más próxima superior en la tabla
            iz_found = next(
                (iz for sec, iz in sections if sec >= s),
                sections[-1][1]
            )
        out.iz_amperes   = iz_found
        out.iz_corrected = round(
            iz_found * out.total_correction, 2
        )

        # Secciones neutro y PE
        if inp.circuit_type == "SINGLE_PHASE":
            out.section_neutral_mm2 = s
        else:
            thr = 16 if inp.conductor_material == "Cu" else 25
            out.section_neutral_mm2 = (
                s if s <= thr
                else normalize_section(
                    s/2, inp.conductor_material
                )
            )

        if   s <= 16: out.section_pe_mm2 = s
        elif s <= 35: out.section_pe_mm2 = 16.0
        else:         out.section_pe_mm2 = normalize_section(s/2)

        # Protección normalizada
        ratings = sorted(
            PROTECTION_RATINGS.get(inp.protection_type, [])
        )
        out.in_protection_amperes = float(
            next(
                (r for r in ratings
                 if r >= out.ib_amperes),
                ratings[-1] if ratings else out.ib_amperes
            )
        )

        # CHECK 1: Ib ≤ In
        out.check_ib_lt_in = (
            out.ib_amperes <= out.in_protection_amperes
        )

        # CHECK 2: In ≤ Iz
        out.check_in_lt_iz = (
            out.in_protection_amperes <= out.iz_corrected
        )

        # Caída de tensión con sección actual
        du, r_km, x_km = calc_vdrop(
            out.ib_amperes, inp.length_m, s,
            inp.conductor_material, inp.insulation_type,
            inp.circuit_type, inp.power_factor
        )
        out.resistivity_ohm_km = round(r_km, 4)
        out.reactance_ohm_km   = round(x_km, 4)
        out.voltage_drop_v     = round(du, 4)
        out.voltage_drop_pct   = round(
            (du / inp.voltage_v) * 100, 4
        )

        # CHECK 3: ΔU%
        out.check_vdrop_ok = (
            out.voltage_drop_pct <= inp.vdrop_limit_pct
        )

        # Cortocircuito
        out.icc_max_origin_ka = inp.icc_origin_ka
        out.icc_end_ka = calc_icc(
            inp.voltage_v, inp.icc_origin_ka,
            s, inp.length_m,
            inp.conductor_material, inp.circuit_type
        )
        out.icc_min_ka = calc_icc_min(
            inp.voltage_v, inp.icc_origin_ka,
            s, out.section_pe_mm2,
            inp.length_m, inp.conductor_material,
            inp.insulation_type, inp.circuit_type
        )

        # CHECK 4: Poder de corte
        pdc = BREAKING_CAPACITY.get(inp.protection_type, 6.0)
        out.check_icc_breaking = (inp.icc_origin_ka <= pdc)

        # CHECK 5: Tiempo cortocircuito térmico
        out.thermal_icc_time_s = calc_thermal_icc_time(
            s, out.icc_end_ka, inp.conductor_material
        )
        out.check_thermal_icc = (out.thermal_icc_time_s >= 0.1)

        # ── ¿Todos los checks pasan? ──────────────────────────
        all_ok = (
            out.check_ib_lt_in
            and out.check_in_lt_iz
            and out.check_vdrop_ok
            and out.check_icc_breaking
            and out.check_thermal_icc
        )

        if all_ok:
            out.overall_ok = True
            break

        # ── Si no pasan, intentar subir sección ──────────────
        # Solo iteramos si el problema es In>Iz o ΔU>límite
        # (el PdC es un problema de la protección, no del cable)
        needs_bigger = (
            not out.check_in_lt_iz
            or not out.check_vdrop_ok
        )

        if needs_bigger:
            # Buscar siguiente sección superior
            next_sections = [
                sec for sec, _ in sections
                if sec > s
            ]
            if next_sections:
                out.section_phase_mm2 = next_sections[0]
                out.warnings.append(
                    f"🔄 Iteración {iteration+1}: "
                    f"S={s}mm² insuficiente → "
                    f"subiendo a {next_sections[0]}mm²"
                )
            else:
                out.warnings.append(
                    "⚠️ Se alcanzó la sección máxima "
                    "disponible en tabla"
                )
                break
        else:
            # El fallo es por PdC (problema de protección)
            out.warnings.append(
                "⚠️ Fallo en PdC: cambiar tipo de protección"
            )
            break

    # Warnings finales
    if not out.check_ib_lt_in:
        out.warnings.append(
            f"❌ Ib={out.ib_amperes:.2f}A "
            f"> In={out.in_protection_amperes:.0f}A"
        )
    if not out.check_in_lt_iz:
        out.warnings.append(
            f"❌ In={out.in_protection_amperes:.0f}A "
            f"> Iz={out.iz_corrected:.2f}A"
        )
    if not out.check_vdrop_ok:
        out.warnings.append(
            f"❌ ΔU={out.voltage_drop_pct:.2f}% "
            f"> límite {inp.vdrop_limit_pct}%"
        )
    if not out.check_icc_breaking:
        pdc = BREAKING_CAPACITY.get(inp.protection_type, 6.0)
        out.warnings.append(
            f"❌ Icc={inp.icc_origin_ka}kA > PdC={pdc}kA"
        )

    return out

# ── INTERFAZ STREAMLIT ────────────────────────────────────────
def main():
    init_db()

    if "results" not in st.session_state:
        st.session_state["results"] = []

    st.markdown(
        "<h1 style='color:#00b4d8'>⚡ ElecCalc Pro</h1>"
        "<p style='color:#888'>UNE-HD 60364-5-52 · REBT 2002"
        " · IEC 60909 | Con optimización iterativa</p><hr>",
        unsafe_allow_html=True
    )

    tab1, tab2, tab3 = st.tabs([
        "⚡ Calcular Circuito",
        "📊 Resumen Sesión",
        "📚 Tablas Normativas"
    ])

    # ── TAB 1: CÁLCULO ────────────────────────────────────────
    with tab1:
        st.markdown("### 📋 Datos del Circuito")
        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("**Identificación**")
            ref   = st.text_input("Referencia", "C-001")
            desc  = st.text_input("Descripción","Motor bomba")
            ctype = st.selectbox("Tipo Circuito",
                ["THREE_PHASE","SINGLE_PHASE"],
                format_func=lambda x:
                "🔴 Trifásico 3F+N+PE"
                if x=="THREE_PHASE"
                else "⚫ Monofásico F+N+PE"
            )
            ltype = st.selectbox("Tipo Carga",
                ["MOTOR","LIGHTING","HEATING","MIXED"],
                format_func=lambda x:{
                    "MOTOR":"⚙️ Motor",
                    "LIGHTING":"💡 Alumbrado",
                    "HEATING":"🔥 Calefacción",
                    "MIXED":"🔀 Mixta"
                }[x]
            )

        with c2:
            st.markdown("**Carga Eléctrica**")
            p_kw   = st.number_input(
                "Potencia (kW) *",
                0.01, 10000.0, 18.5, 0.5
            )
            cosphi = st.slider("cosφ", 0.5, 1.0, 0.88, 0.01)
            eta    = st.slider("η Rendimiento",
                               0.5, 1.0, 0.86, 0.01)
            u_v    = st.selectbox(
                "Tensión (V)", [230.0,400.0,690.0], index=1
            )
            fd     = st.number_input(
                "Factor Demanda fd", 0.1, 1.0, 1.0, 0.05
            )
            fs     = st.number_input(
                "Factor Simultaneidad fs",
                0.1, 1.0, 1.0, 0.05
            )

        with c3:
            st.markdown("**Conductor e Instalación**")
            mat  = st.selectbox(
                "Material", ["Cu","Al"],
                format_func=lambda x:
                "🔶 Cobre (Cu)" if x=="Cu"
                else "⚪ Aluminio (Al)"
            )
            ins  = st.selectbox(
                "Aislamiento",
                ["XLPE","PVC"],
                format_func=lambda x:
                f"{x} ({'90°C' if x=='XLPE' else '70°C'})"
            )
            meth = st.selectbox(
                "Método Instalación",
                ["A1","B1","C","E"],
                index=3,  # E por defecto (como tu Excel)
                format_func=lambda x:{
                    "A1":"A1 – Empotrado en aislante",
                    "B1":"B1 – Tubo en pared/techo",
                    "C":"C  – Cable sobre pared",
                    "E":"E  – Bandeja al aire libre"
                }[x]
            )
            lm   = st.number_input(
                "Longitud (m) *", 0.1, 10000.0, 100.0, 5.0
            )
            temp = st.number_input(
                "T° Ambiente (°C)", 10, 75, 40, 5
            )
            ngr  = st.number_input(
                "Nº Circuitos Agrupados", 1, 30, 3, 1
            )
            arr  = st.selectbox(
                "Disposición Agrupamiento",
                ["touching","spaced"],
                format_func=lambda x:
                "En contacto" if x=="touching"
                else "Espaciados (≥1D)"
            )

        st.markdown("---")
        p1, p2, p3 = st.columns(3)
        with p1:
            prot = st.selectbox("Tipo Protección",
                ["MCB","MCCB","FUSE"],
                format_func=lambda x:{
                    "MCB":"MCB – Magnetotérmico",
                    "MCCB":"MCCB – Caja Moldeada",
                    "FUSE":"FUSE – Fusible"
                }[x]
            )
        with p2:
            icc_orig = st.number_input(
                "Icc cabecera (kA)",
                0.1, 150.0, 40.0, 1.0
            )
        with p3:
            vdrop_lim = st.number_input(
                "Límite ΔU (%)",
                0.5, 10.0, 5.0, 0.5,
                help="5% motores / 3% alumbrado (REBT)"
            )

        if st.button("⚡ CALCULAR Y OPTIMIZAR",
                     type="primary",
                     use_container_width=True):
            inp = CircuitInput(
                circuit_ref=ref, description=desc,
                circuit_type=ctype, load_type=ltype,
                power_kw=p_kw, power_factor=cosphi,
                efficiency=eta, demand_factor=fd,
                simultaneity_factor=fs,
                voltage_v=u_v, conductor_material=mat,
                insulation_type=ins,
                installation_method=meth,
                length_m=lm, ambient_temp_c=temp,
                num_grouped_circuits=ngr,
                grouping_arrangement=arr,
                icc_origin_ka=icc_orig,
                protection_type=prot,
                vdrop_limit_pct=vdrop_lim
            )

            with st.spinner(
                "Calculando y optimizando sección..."
            ):
                out = calculate(inp)

            st.markdown("---")
            st.markdown("### 📊 Resultados")

            if out.errors:
                for e in out.errors:
                    st.error(e)
            else:
                # Banner estado
                col_st = "#4ade80" if out.overall_ok \
                         else "#f87171"
                st.markdown(
                    f"<div style='border-left:4px solid "
                    f"{col_st};padding:0.8rem 1.2rem;"
                    f"background:#1a2332;"
                    f"border-radius:0 8px 8px 0;"
                    f"margin-bottom:1rem'>"
                    f"<span style='color:{col_st};"
                    f"font-size:1.2rem;font-weight:700'>"
                    f"{'✅ CIRCUITO CORRECTO'if out.overall_ok else '❌ CIRCUITO CON PROBLEMAS'}"
                    f"</span>"
                    f"<span style='color:#888;"
                    f"font-size:0.85rem;margin-left:1rem'>"
                    f"Sección limitada por: "
                    f"<b>{out.section_limited_by}</b> · "
                    f"Iteraciones: {out.iterations}"
                    f"</span></div>",
                    unsafe_allow_html=True
                )

                # KPIs principales
                k = st.columns(7)
                k[0].metric("Ib (A)",
                            f"{out.ib_amperes:.2f}")
                k[1].metric("In prot. (A)",
                            f"{out.in_protection_amperes:.0f}")
                k[2].metric("S fase (mm²)",
                            f"{out.section_phase_mm2:.0f}",
                            delta=f"S_térm={out.section_thermal_mm2:.0f} / S_ΔU={out.section_vdrop_mm2:.0f}")
                k[3].metric("Iz real (A)",
                            f"{out.iz_corrected:.2f}")
                k[4].metric("ΔU (%)",
                            f"{out.voltage_drop_pct:.2f}",
                            delta=f"límite {inp.vdrop_limit_pct}%",
                            delta_color="inverse")
                k[5].metric("Icc fin (kA)",
                            f"{out.icc_end_ka:.3f}")
                k[6].metric("t_cc (s)",
                            f"{out.thermal_icc_time_s:.3f}")

                # Tabla de verificaciones CHECK
                st.markdown("#### 🔍 Verificaciones Normativas")
                checks_data = [
                    {
                        "Verificación": "Ib ≤ In",
                        "Valor": f"{out.ib_amperes:.2f} A",
                        "Límite": f"{out.in_protection_amperes:.0f} A",
                        "Resultado": "✅ OK" if out.check_ib_lt_in else "❌ NOT OK",
                        "Norma": "UNE-HD 60364-4-43 §433"
                    },
                    {
                        "Verificación": "In ≤ Iz (corregida)",
                        "Valor": f"{out.in_protection_amperes:.0f} A",
                        "Límite": f"{out.iz_corrected:.2f} A",
                        "Resultado": "✅ OK" if out.check_in_lt_iz else "❌ NOT OK",
                        "Norma": "UNE-HD 60364-4-43 §433"
                    },
                    {
                        "Verificación": f"ΔU ≤ {inp.vdrop_limit_pct}%",
                        "Valor": f"{out.voltage_drop_pct:.3f}%",
                        "Límite": f"{inp.vdrop_limit_pct}%",
                        "Resultado": "✅ OK" if out.check_vdrop_ok else "❌ NOT OK",
                        "Norma": "REBT ITC-BT-19"
                    },
                    {
                        "Verificación": "Icc ≤ PdC",
                        "Valor": f"{inp.icc_origin_ka:.2f} kA",
                        "Límite": f"{BREAKING_CAPACITY.get(inp.protection_type,6):.0f} kA",
                        "Resultado": "✅ OK" if out.check_icc_breaking else "❌ NOT OK",
                        "Norma": "IEC 60947 / IEC 60898"
                    },
                    {
                        "Verificación": "t_cc térmico ≥ 0.1s",
                        "Valor": f"{out.thermal_icc_time_s:.4f} s",
                        "Límite": "0.1 s",
                        "Resultado": "✅ OK" if out.check_thermal_icc else "❌ NOT OK",
                        "Norma": "IEC 60364-4-43 §434"
                    },
                ]
                df_checks = pd.DataFrame(checks_data)

                def color_check(val):
                    if "✅" in str(val):
                        return "color: #4ade80; font-weight:bold"
                    elif "❌" in str(val):
                        return "color: #f87171; font-weight:bold"
                    return ""

                st.dataframe(
                    df_checks.style.applymap(
                        color_check, subset=["Resultado"]
                    ),
                    use_container_width=True,
                    hide_index=True
                )

                # Detalle técnico completo
                with st.expander(
                    "📐 Detalle Técnico Completo", expanded=True
                ):
                    d1,d2,d3,d4 = st.columns(4)

                    with d1:
                        st.markdown("**🔧 Corrección**")
                        st.write(f"kt = **{out.kt:.4f}**")
                        st.write(f"kg = **{out.kg:.4f}**")
                        st.write(
                            f"fc = **{out.total_correction:.4f}**"
                        )
                        st.write(
                            f"Ib' = **{out.ib_corrected:.3f} A**"
                        )

                    with d2:
                        st.markdown("**📏 Secciones**")
                        st.write(
                            f"S térmica: **{out.section_thermal_mm2} mm²**"
                        )
                        st.write(
                            f"S ΔU:      **{out.section_vdrop_mm2} mm²**"
                        )
                        st.write(
                            f"**S FINAL:  {out.section_phase_mm2} mm²**"
                        )
                        st.write(
                            f"S neutro:  **{out.section_neutral_mm2} mm²**"
                        )
                        st.write(
                            f"S PE:      **{out.section_pe_mm2} mm²**"
                        )

                    with d3:
                        st.markdown("**⚡ Caída de Tensión**")
                        st.write(
                            f"R = **{out.resistivity_ohm_km:.4f} Ω/km**"
                        )
                        st.write(
                            f"X = **{out.reactance_ohm_km:.4f} Ω/km**"
                        )
                        st.write(
                            f"ΔU = **{out.voltage_drop_v:.4f} V**"
                        )
                        st.write(
                            f"ΔU = **{out.voltage_drop_pct:.3f}%**"
                        )
                        st.write(
                            f"Límite: **{inp.vdrop_limit_pct}%**"
                        )

                    with d4:
                        st.markdown("**⚡ Cortocircuito**")
                        st.write(
                            f"Icc origen: **{inp.icc_origin_ka:.2f} kA**"
                        )
                        st.write(
                            f"Icc fin (max): **{out.icc_end_ka:.4f} kA**"
                        )
                        st.write(
                            f"Icc fin (min): **{out.icc_min_ka:.4f} kA**"
                        )
                        st.write(
                            f"t_cc adm: **{out.thermal_icc_time_s:.4f} s**"
                        )
                        st.write(
                            f"Iz tabla: **{out.iz_amperes:.1f} A**"
                        )
                        st.write(
                            f"Iz real: **{out.iz_corrected:.2f} A**"
                        )

                if out.warnings:
                    with st.expander(
                        f"⚠️ {len(out.warnings)} Aviso(s) / "
                        f"Iteraciones"
                    ):
                        for w in out.warnings:
                            st.warning(w)

                st.session_state["results"].append((inp, out))
                st.success("✅ Guardado en Resumen de Sesión")

    # ── TAB 2: RESUMEN ────────────────────────────────────────
    with tab2:
        res = st.session_state.get("results", [])
        if not res:
            st.info("Calcula circuitos para ver el resumen.")
        else:
            st.markdown(
                f"### {len(res)} Circuito(s) calculados"
            )
            rows = []
            for i, o in res:
                e = bool(o.errors)
                rows.append({
                    "Ref":       i.circuit_ref,
                    "Desc":      i.description,
                    "P(kW)":     f"{i.power_kw:.2f}",
                    "L(m)":      f"{i.length_m:.0f}",
                    "Ib(A)":     f"{o.ib_amperes:.2f}" if not e else "-",
                    "kt·kg":     f"{o.total_correction:.3f}" if not e else "-",
                    "S_term":    f"{o.section_thermal_mm2:.0f}" if not e else "-",
                    "S_ΔU":      f"{o.section_vdrop_mm2:.0f}" if not e else "-",
                    "S_FINAL":   f"{o.section_phase_mm2:.0f}" if not e else "-",
                    "S_N":       f"{o.section_neutral_mm2:.0f}" if not e else "-",
                    "S_PE":      f"{o.section_pe_mm2:.0f}" if not e else "-",
                    "Iz(A)":     f"{o.iz_corrected:.1f}" if not e else "-",
                    "In(A)":     f"{o.in_protection_amperes:.0f}" if not e else "-",
                    "ΔU(%)":     f"{o.voltage_drop_pct:.2f}" if not e else "-",
                    "Icc_fin":   f"{o.icc_end_ka:.3f}" if not e else "-",
                    "Limitado":  o.section_limited_by if not e else "-",
                    "Iter":      o.iterations if not e else "-",
                    "✓Ib≤In":   "✅" if o.check_ib_lt_in else "❌",
                    "✓In≤Iz":   "✅" if o.check_in_lt_iz else "❌",
                    "✓ΔU":      "✅" if o.check_vdrop_ok else "❌",
                    "✓PdC":     "✅" if o.check_icc_breaking else "❌",
                    "✓t_cc":    "✅" if o.check_thermal_icc else "❌",
                    "ESTADO":    "✅OK" if o.overall_ok else (
                        "⚠️ERR" if o.errors else "❌NOK"
                    ),
                })

            df = pd.DataFrame(rows)
            st.dataframe(
                df, use_container_width=True,
                hide_index=True, height=450
            )

            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(
                    w, index=False, sheet_name="Circuitos"
                )
            st.download_button(
                "📥 Exportar Excel",
                data=buf.getvalue(),
                file_name="calculo_electrico.xlsx",
                mime="application/vnd.openxmlformats-"
                     "officedocument.spreadsheetml.sheet"
            )
            if st.button("🗑️ Limpiar sesión"):
                st.session_state["results"] = []
                st.rerun()

    # ── TAB 3: TABLAS ─────────────────────────────────────────
    with tab3:
        st.markdown("### 📚 Tablas UNE-HD 60364-5-52")
        s1, s2, s3 = st.tabs([
            "Capacidades de Corriente",
            "Factores Temperatura",
            "Factores Agrupamiento"
        ])
        with s1:
            f1,f2,f3 = st.columns(3)
            mf = f1.selectbox(
                "Método",["A1","B1","C","E"],key="m"
            )
            if_ = f2.selectbox(
                "Aislamiento",["XLPE","PVC"],key="i"
            )
            nf = f3.selectbox(
                "Conductores",[2,3],key="n",
                format_func=lambda x:
                f"{x} conductores cargados"
            )
            secs = get_sections(mf, if_, nf)
            if secs:
                st.dataframe(
                    pd.DataFrame(
                        secs,
                        columns=["Sección (mm²)","Imax (A)"]
                    ),
                    use_container_width=True,
                    hide_index=True
                )
            else:
                st.warning("Sin datos para esa combinación.")
        with s2:
            it = st.selectbox(
                "Aislamiento",["XLPE","PVC"],key="t"
            )
            st.dataframe(
                pd.DataFrame([
                    {"T_amb(°C)":t, "kt":get_kt(it,t)}
                    for t in range(10,76,5)
                ]),
                use_container_width=True, hide_index=True
            )
        with s3:
            ar = st.selectbox(
                "Disposición",
                ["touching","spaced"],key="a",
                format_func=lambda x:
                "En contacto" if x=="touching"
                else "Espaciados"
            )
            st.dataframe(
                pd.DataFrame([
                    {"N° circuitos":n, "kg":get_kg(n,ar)}
                    for n in [1,2,3,4,5,6,7,8,9,12,16,20]
                ]),
                use_container_width=True, hide_index=True
            )

if __name__ == "__main__":
    main()
