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

SQRT3         = math.sqrt(3)
TEMP_COEFF_CU = 0.00393
TEMP_COEFF_AL = 0.00403
DB_PATH       = "eleccalc.db"

PROTECTION_RATINGS = {
    "MCB":  [6,10,13,16,20,25,32,40,50,63],
    "MCCB": [16,20,25,32,40,50,63,80,100,
             125,160,200,250,315,400,500,630],
    "FUSE": [2,4,6,10,16,20,25,32,40,50,
             63,80,100,125,160,200,250,315,400],
}
VDROP_LIMITS = {
    "MOTOR":5.0,"LIGHTING":3.0,
    "HEATING":3.0,"MIXED":3.0
}
BREAKING_CAPACITY = {
    "MCB":6.0,"MCCB":25.0,"FUSE":80.0
}
STANDARD_SECTIONS = [
    1.5,2.5,4.0,6.0,10.0,16.0,25.0,35.0,
    50.0,70.0,95.0,120.0,150.0,185.0,240.0,300.0
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
    vdrop_limit_pct:      float = 5.0

@dataclass
class CircuitOutput:
    ib_amperes:            float = 0.0
    kt:                    float = 1.0
    kg:                    float = 1.0
    total_correction:      float = 1.0
    ib_corrected:          float = 0.0
    section_thermal_mm2:   float = 0.0
    iz_thermal_amperes:    float = 0.0
    iz_thermal_corrected:  float = 0.0
    section_vdrop_mm2:     float = 0.0
    section_phase_mm2:     float = 0.0
    section_neutral_mm2:   float = 0.0
    section_pe_mm2:        float = 0.0
    iz_amperes:            float = 0.0
    iz_corrected:          float = 0.0
    in_protection_amperes: float = 0.0
    resistivity_ohm_km:    float = 0.0
    reactance_ohm_km:      float = 0.0
    voltage_drop_v:        float = 0.0
    voltage_drop_pct:      float = 0.0
    vdrop_limit_pct:       float = 5.0
    icc_max_origin_ka:     float = 0.0
    icc_end_ka:            float = 0.0
    icc_min_ka:            float = 0.0
    thermal_icc_time_s:    float = 0.0
    check_ib_lt_in:        bool  = False
    check_in_lt_iz:        bool  = False
    check_vdrop_ok:        bool  = False
    check_icc_breaking:    bool  = False
    check_thermal_icc:     bool  = False
    overall_ok:            bool  = False
    section_limited_by:    str   = ""
    warnings:              list  = field(default_factory=list)
    errors:                list  = field(default_factory=list)
    iterations:            int   = 0

    @property
    def status(self):
        if self.errors:     return "ERROR"
        if self.overall_ok: return "OK"
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
    r_km, x_km = get_impedance(material, section)
    t_max = 70.0 if insulation == "PVC" else 90.0
    alpha = TEMP_COEFF_CU if material=="Cu" else TEMP_COEFF_AL
    r_km_t = r_km * (1 + alpha*(t_max-20))
    r_m = r_km_t/1000.0
    x_m = x_km/1000.0
    cosp = power_factor
    sinp = math.sqrt(max(0, 1-cosp**2))
    fac  = SQRT3 if circuit_type=="THREE_PHASE" else 2.0
    if section < 50:
        du = fac * ib * length_m * r_m
    else:
        du = fac * ib * length_m * (r_m*cosp + x_m*sinp)
    return du, r_km_t, x_km

def find_section_for_vdrop(ib, length_m, voltage_v,
                            material, insulation,
                            circuit_type, power_factor,
                            vdrop_limit_pct):
    std = STANDARD_SECTIONS.copy()
    if material == "Al":
        std = [x for x in std if x >= 16]
    for s in std:
        du, _, _ = calc_vdrop(
            ib, length_m, s, material,
            insulation, circuit_type, power_factor
        )
        if (du/voltage_v)*100 <= vdrop_limit_pct:
            return s
    return std[-1]

def calc_icc_end(voltage_v, icc_origin_ka,
                 section, length_m,
                 material, circuit_type):
    if circuit_type == "THREE_PHASE":
        z_o = voltage_v/(SQRT3*icc_origin_ka*1000)
    else:
        z_o = voltage_v/(2*icc_origin_ka*1000)
    r0, x0 = get_impedance(material, section)
    fz = 1 if circuit_type=="THREE_PHASE" else 2
    zr = z_o + fz*(r0/1000)*length_m
    zx = fz*(x0/1000)*length_m
    zt = math.sqrt(zr**2+zx**2)
    if circuit_type == "THREE_PHASE":
        return round((voltage_v/(SQRT3*zt))/1000, 4)
    return round((voltage_v/(2*zt))/1000, 4)

def calc_icc_min(voltage_v, section_phase,
                 section_pe, length_m,
                 material, insulation, circuit_type):
    u_phase = (voltage_v/SQRT3
               if circuit_type=="THREE_PHASE"
               else voltage_v)
    t_max = 70.0 if insulation=="PVC" else 90.0
    alpha = TEMP_COEFF_CU if material=="Cu" else TEMP_COEFF_AL
    corr  = 1+alpha*(t_max-20)
    r_ph,_ = get_impedance(material, section_phase)
    r_pe,_ = get_impedance(material, section_pe)
    z_loop = ((r_ph+r_pe)/1000)*corr*length_m
    if z_loop <= 0:
        return 0.0
    return round((u_phase/z_loop)/1000, 4)

def calc_thermal_time(section, icc_ka, material):
    k = 143 if material=="Cu" else 94
    if icc_ka <= 0:
        return 999.0
    return round((k*section/(icc_ka*1000))**2, 4)

# ── MOTOR DE CÁLCULO ──────────────────────────────────────────
def calculate(inp: CircuitInput) -> CircuitOutput:
    out = CircuitOutput()
    out.vdrop_limit_pct = inp.vdrop_limit_pct

    # PASO 1: Ib
    p_w   = (inp.power_kw*1000
             *inp.demand_factor
             *inp.simultaneity_factor)
    denom = inp.power_factor * inp.efficiency
    if denom <= 0:
        out.errors.append("cosφ·η debe ser > 0")
        return out

    ib = (p_w/(SQRT3*inp.voltage_v*denom)
          if inp.circuit_type=="THREE_PHASE"
          else p_w/(inp.voltage_v*denom))

    if inp.load_type == "MOTOR":
        ib *= 1.25
        out.warnings.append(
            "Motor: Ib×1.25 aplicado (REBT ITC-BT-47)"
        )
    out.ib_amperes = round(ib, 4)

    # PASO 2: Factores corrección
    out.kt = get_kt(inp.insulation_type, inp.ambient_temp_c)
    out.kg = get_kg(
        inp.num_grouped_circuits, inp.grouping_arrangement
    )
    out.total_correction = round(out.kt*out.kg, 4)
    if out.total_correction <= 0:
        out.errors.append("Factor de corrección = 0")
        return out
    out.ib_corrected = round(
        out.ib_amperes/out.total_correction, 4
    )

    n_cond = 2 if inp.circuit_type=="SINGLE_PHASE" else 3

    # PASO 3: Sección térmica
    sections = get_sections(
        inp.installation_method,
        inp.insulation_type, n_cond
    )
    if not sections:
        out.errors.append(
            f"Sin datos tabla: "
            f"{inp.installation_method}/"
            f"{inp.insulation_type}/{n_cond}cond"
        )
        return out

    found = next(
        ((s,iz) for s,iz in sections
         if iz >= out.ib_corrected), None
    )
    if found is None:
        out.errors.append(
            f"Ninguna sección soporta "
            f"Ib'={out.ib_corrected:.1f}A"
        )
        return out

    out.section_thermal_mm2  = found[0]
    out.iz_thermal_amperes   = found[1]
    out.iz_thermal_corrected = round(
        found[1]*out.total_correction, 2
    )

    # PASO 4: Sección por ΔU
    out.section_vdrop_mm2 = find_section_for_vdrop(
        out.ib_amperes, inp.length_m, inp.voltage_v,
        inp.conductor_material, inp.insulation_type,
        inp.circuit_type, inp.power_factor,
        inp.vdrop_limit_pct
    )

    # PASO 5: Sección final
    s_min = 1.5 if inp.load_type=="LIGHTING" else 2.5
    s_final = max(
        out.section_thermal_mm2,
        out.section_vdrop_mm2,
        s_min
    )

    if (out.section_vdrop_mm2 > out.section_thermal_mm2):
        out.section_limited_by = "CAÍDA DE TENSIÓN"
        out.warnings.append(
            f"⚡ S_térmica={out.section_thermal_mm2}mm² "
            f"→ S_ΔU={out.section_vdrop_mm2}mm² "
            f"(limitado por caída de tensión)"
        )
    else:
        out.section_limited_by = "CAPACIDAD TÉRMICA"

    out.section_phase_mm2 = s_final

    # PASO 6: Iteración automática
    MAX_ITER = len(STANDARD_SECTIONS)

    for iteration in range(MAX_ITER):
        out.iterations = iteration + 1
        s = out.section_phase_mm2

        # Iz con sección actual
        iz_row = next(
            ((sec,iz) for sec,iz in sections if sec >= s),
            sections[-1]
        )
        # Si la sección exacta no está en tabla, usar superior
        if iz_row[0] != s:
            # Recalcular con sección de tabla más próxima superior
            s_tabla = iz_row[0]
        else:
            s_tabla = s

        out.iz_amperes   = iz_row[1]
        out.iz_corrected = round(
            iz_row[1]*out.total_correction, 2
        )

        # Neutro y PE
        if inp.circuit_type == "SINGLE_PHASE":
            out.section_neutral_mm2 = s
        else:
            thr = 16 if inp.conductor_material=="Cu" else 25
            out.section_neutral_mm2 = (
                s if s <= thr
                else normalize_section(
                    s/2, inp.conductor_material
                )
            )
        if   s <= 16: out.section_pe_mm2 = s
        elif s <= 35: out.section_pe_mm2 = 16.0
        else:         out.section_pe_mm2 = normalize_section(s/2)

        # Protección
        ratings = sorted(
            PROTECTION_RATINGS.get(inp.protection_type,[])
        )
        out.in_protection_amperes = float(
            next(
                (r for r in ratings
                 if r >= out.ib_amperes),
                ratings[-1] if ratings else out.ib_amperes
            )
        )

        # Checks corriente
        out.check_ib_lt_in = (
            out.ib_amperes <= out.in_protection_amperes
        )
        out.check_in_lt_iz = (
            out.in_protection_amperes <= out.iz_corrected
        )

        # Caída de tensión
        du, r_km, x_km = calc_vdrop(
            out.ib_amperes, inp.length_m, s,
            inp.conductor_material, inp.insulation_type,
            inp.circuit_type, inp.power_factor
        )
        out.resistivity_ohm_km = round(r_km, 4)
        out.reactance_ohm_km   = round(x_km, 4)
        out.voltage_drop_v     = round(du, 4)
        out.voltage_drop_pct   = round(
            (du/inp.voltage_v)*100, 4
        )
        out.check_vdrop_ok = (
            out.voltage_drop_pct <= inp.vdrop_limit_pct
        )

        # Icc
        out.icc_max_origin_ka = inp.icc_origin_ka
        out.icc_end_ka = calc_icc_end(
            inp.voltage_v, inp.icc_origin_ka,
            s, inp.length_m,
            inp.conductor_material, inp.circuit_type
        )
        out.icc_min_ka = calc_icc_min(
            inp.voltage_v, s,
            out.section_pe_mm2, inp.length_m,
            inp.conductor_material, inp.insulation_type,
            inp.circuit_type
        )

        # Check PdC (NO itera cable, es problema de protección)
        pdc = BREAKING_CAPACITY.get(inp.protection_type, 6.0)
        out.check_icc_breaking = (inp.icc_origin_ka <= pdc)

        # Check térmico
        out.thermal_icc_time_s = calc_thermal_time(
            s, out.icc_end_ka, inp.conductor_material
        )
        out.check_thermal_icc = (out.thermal_icc_time_s >= 0.1)

        # ¿Checks de CABLE OK?
        cable_ok = (
            out.check_ib_lt_in
            and out.check_in_lt_iz
            and out.check_vdrop_ok
            and out.check_thermal_icc
        )

        if cable_ok:
            # El check PdC es de protección, no de cable
            out.overall_ok = (
                cable_ok and out.check_icc_breaking
            )
            break

        # Necesita sección mayor
        next_s = next(
            (sec for sec,_ in sections if sec > s), None
        )
        if next_s:
            out.warnings.append(
                f"🔄 Iter {iteration+1}: "
                f"S={s}mm² → subiendo a {next_s}mm²"
            )
            out.section_phase_mm2 = next_s
        else:
            out.warnings.append(
                "⚠️ Sección máxima alcanzada en tabla"
            )
            out.overall_ok = False
            break

    # Avisos finales
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
            f"> {inp.vdrop_limit_pct}%"
        )
    if not out.check_icc_breaking:
        pdc = BREAKING_CAPACITY.get(inp.protection_type,6.0)
        out.warnings.append(
            f"⚠️ PdC: Icc_origen={inp.icc_origin_ka}kA "
            f"> PdC_{inp.protection_type}={pdc}kA → "
            f"Cambiar a MCCB o FUSE"
        )

    return out

# ── INTERFAZ ──────────────────────────────────────────────────
def main():
    init_db()

    if "results" not in st.session_state:
        st.session_state["results"] = []

    st.markdown(
        "<h1 style='color:#00b4d8'>⚡ ElecCalc Pro</h1>"
        "<p style='color:#888'>UNE-HD 60364-5-52 · "
        "REBT 2002 · IEC 60909</p><hr>",
        unsafe_allow_html=True
    )

    tab1, tab2, tab3 = st.tabs([
        "⚡ Calcular","📊 Resumen","📚 Tablas"
    ])

    with tab1:
        st.markdown("### 📋 Datos del Circuito")
        c1,c2,c3 = st.columns(3)

        with c1:
            ref   = st.text_input("Referencia","C-001")
            desc  = st.text_input("Descripción","Motor bomba")
            ctype = st.selectbox("Tipo Circuito",
                ["THREE_PHASE","SINGLE_PHASE"],
                format_func=lambda x:
                "🔴 Trifásico" if x=="THREE_PHASE"
                else "⚫ Monofásico"
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
            p_kw  = st.number_input(
                "Potencia (kW)*",0.01,10000.0,18.5,0.5
            )
            cosphi = st.slider("cosφ",0.5,1.0,0.88,0.01)
            eta    = st.slider("η",0.5,1.0,0.86,0.01)
            u_v    = st.selectbox(
                "Tensión (V)",[230.0,400.0,690.0],index=1
            )
            fd = st.number_input("Factor Demanda",0.1,1.0,1.0,0.05)
            fs = st.number_input("Factor Simult.",0.1,1.0,1.0,0.05)

        with c3:
            mat  = st.selectbox("Material",["Cu","Al"])
            ins  = st.selectbox("Aislamiento",["XLPE","PVC"])
            meth = st.selectbox("Método Instalación",
                ["A1","B1","C","E"], index=3,
                format_func=lambda x:{
                    "A1":"A1-Empotrado aislante",
                    "B1":"B1-Tubo pared",
                    "C":"C-Sobre pared",
                    "E":"E-Bandeja aire"
                }[x]
            )
            lm   = st.number_input(
                "Longitud (m)*",0.1,10000.0,100.0,5.0
            )
            temp = st.number_input("T°amb (°C)",10,75,40,5)
            ngr  = st.number_input("Nº Agrupados",1,30,3,1)
            arr  = st.selectbox("Disposición",
                ["touching","spaced"],
                format_func=lambda x:
                "En contacto" if x=="touching"
                else "Espaciados"
            )

        st.markdown("---")
        p1,p2,p3 = st.columns(3)
        with p1:
            prot = st.selectbox("Protección",
                ["MCB","MCCB","FUSE"],
                format_func=lambda x:{
                    "MCB":"MCB-Magnetotérmico (PdC=6kA)",
                    "MCCB":"MCCB-Caja Moldeada (PdC=25kA)",
                    "FUSE":"FUSE-Fusible (PdC=80kA)"
                }[x]
            )
        with p2:
            icc_orig = st.number_input(
                "Icc cabecera (kA)",0.1,150.0,40.0,1.0
            )
        with p3:
            vdrop_lim = st.number_input(
                "Límite ΔU (%)",0.5,10.0,5.0,0.5
            )

        # INFO sobre PdC
        pdc_val = BREAKING_CAPACITY.get(prot, 6.0)
        if icc_orig > pdc_val:
            st.warning(
                f"⚠️ **PdC insuficiente**: "
                f"Icc={icc_orig}kA > PdC_{prot}={pdc_val}kA. "
                f"Considera usar **MCCB** (25kA) o **FUSE** (80kA)"
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

            with st.spinner("Calculando..."):
                out = calculate(inp)

            st.markdown("---")
            st.markdown("### 📊 Resultados")

            if out.errors:
                for e in out.errors:
                    st.error(e)
            else:
                col_st = "#4ade80" if out.overall_ok \
                         else "#f87171"
                icon = "✅" if out.overall_ok else "❌"

                # Razón del fallo
                fail_reasons = []
                if not out.check_ib_lt_in:
                    fail_reasons.append("Ib>In")
                if not out.check_in_lt_iz:
                    fail_reasons.append("In>Iz")
                if not out.check_vdrop_ok:
                    fail_reasons.append(
                        f"ΔU={out.voltage_drop_pct:.2f}%>"
                        f"{inp.vdrop_limit_pct}%"
                    )
                if not out.check_icc_breaking:
                    fail_reasons.append(
                        f"PdC insuficiente "
                        f"({inp.icc_origin_ka}kA>"
                        f"{BREAKING_CAPACITY.get(prot,6)}kA)"
                    )

                fail_txt = (
                    " | ".join(fail_reasons)
                    if fail_reasons else ""
                )

                st.markdown(
                    f"<div style='border-left:4px solid "
                    f"{col_st};padding:0.8rem 1.2rem;"
                    f"background:#1a2332;"
                    f"border-radius:0 8px 8px 0;"
                    f"margin-bottom:1rem'>"
                    f"<b style='color:{col_st};"
                    f"font-size:1.1rem'>"
                    f"{icon} "
                    f"{'CORRECTO' if out.overall_ok else 'CON PROBLEMAS: '+fail_txt}"
                    f"</b><br>"
                    f"<span style='color:#888;"
                    f"font-size:0.8rem'>"
                    f"Limitado por: {out.section_limited_by}"
                    f" · Iteraciones: {out.iterations}"
                    f"</span></div>",
                    unsafe_allow_html=True
                )

                k = st.columns(7)
                k[0].metric("Ib (A)",
                            f"{out.ib_amperes:.2f}")
                k[1].metric("In (A)",
                            f"{out.in_protection_amperes:.0f}")
                k[2].metric("S fase (mm²)",
                            f"{out.section_phase_mm2:.0f}",
                            delta=(
                                f"T={out.section_thermal_mm2:.0f}"
                                f"/ΔU={out.section_vdrop_mm2:.0f}"
                            ))
                k[3].metric("Iz real (A)",
                            f"{out.iz_corrected:.2f}")
                k[4].metric("ΔU (%)",
                            f"{out.voltage_drop_pct:.2f}",
                            delta=f"lím {inp.vdrop_limit_pct}%",
                            delta_color="inverse")
                k[5].metric("Icc fin (kA)",
                            f"{out.icc_end_ka:.3f}")
                k[6].metric("t_cc (s)",
                            f"{out.thermal_icc_time_s:.3f}")

                # ── TABLA CHECKS ─────────────────────────────
                st.markdown("#### 🔍 Verificaciones")
                pdc = BREAKING_CAPACITY.get(prot, 6.0)
                checks_data = [
                    {
                        "Check": "Ib ≤ In",
                        "Valor": f"{out.ib_amperes:.2f} A",
                        "Límite": f"{out.in_protection_amperes:.0f} A",
                        "Estado": "✅ OK" if out.check_ib_lt_in else "❌ NOT OK",
                        "Norma": "§433.1"
                    },
                    {
                        "Check": "In ≤ Iz",
                        "Valor": f"{out.in_protection_amperes:.0f} A",
                        "Límite": f"{out.iz_corrected:.2f} A",
                        "Estado": "✅ OK" if out.check_in_lt_iz else "❌ NOT OK",
                        "Norma": "§433.1"
                    },
                    {
                        "Check": f"ΔU ≤ {inp.vdrop_limit_pct}%",
                        "Valor": f"{out.voltage_drop_pct:.3f}%",
                        "Límite": f"{inp.vdrop_limit_pct}%",
                        "Estado": "✅ OK" if out.check_vdrop_ok else "❌ NOT OK",
                        "Norma": "ITC-BT-19"
                    },
                    {
                        "Check": f"Icc ≤ PdC ({prot})",
                        "Valor": f"{inp.icc_origin_ka:.2f} kA",
                        "Límite": f"{pdc:.0f} kA",
                        "Estado": "✅ OK" if out.check_icc_breaking else "❌ NOT OK",
                        "Norma": "IEC 60947"
                    },
                    {
                        "Check": "t_cc ≥ 0.1s",
                        "Valor": f"{out.thermal_icc_time_s:.4f} s",
                        "Límite": "0.1 s",
                        "Estado": "✅ OK" if out.check_thermal_icc else "❌ NOT OK",
                        "Norma": "§434"
                    },
                ]
                df_ch = pd.DataFrame(checks_data)

                # ✅ FIX: usar .map() en vez de .applymap()
                def color_check(val):
                    if "✅" in str(val):
                        return "color:#4ade80;font-weight:bold"
                    if "❌" in str(val):
                        return "color:#f87171;font-weight:bold"
                    return ""

                try:
                    # pandas >= 2.1
                    styled = df_ch.style.map(
                        color_check, subset=["Estado"]
                    )
                except AttributeError:
                    # pandas < 2.1
                    styled = df_ch.style.applymap(
                        color_check, subset=["Estado"]
                    )

                st.dataframe(
                    styled,
                    use_container_width=True,
                    hide_index=True
                )

                # Detalle técnico
                with st.expander("📐 Detalle Técnico"):
                    d1,d2,d3,d4 = st.columns(4)
                    with d1:
                        st.markdown("**Corrección**")
                        st.write(f"kt = {out.kt:.4f}")
                        st.write(f"kg = {out.kg:.4f}")
                        st.write(f"fc = {out.total_correction:.4f}")
                        st.write(f"Ib' = {out.ib_corrected:.3f} A")
                    with d2:
                        st.markdown("**Secciones**")
                        st.write(f"S térmica: {out.section_thermal_mm2} mm²")
                        st.write(f"S ΔU:      {out.section_vdrop_mm2} mm²")
                        st.write(f"**S FINAL: {out.section_phase_mm2} mm²**")
                        st.write(f"S neutro:  {out.section_neutral_mm2} mm²")
                        st.write(f"S PE:      {out.section_pe_mm2} mm²")
                    with d3:
                        st.markdown("**Caída Tensión**")
                        st.write(f"R = {out.resistivity_ohm_km:.4f} Ω/km")
                        st.write(f"X = {out.reactance_ohm_km:.4f} Ω/km")
                        st.write(f"ΔU = {out.voltage_drop_v:.4f} V")
                        st.write(f"ΔU = {out.voltage_drop_pct:.3f}%")
                    with d4:
                        st.markdown("**Cortocircuito**")
                        st.write(f"Icc orig: {inp.icc_origin_ka:.2f} kA")
                        st.write(f"Icc fin:  {out.icc_end_ka:.4f} kA")
                        st.write(f"Icc min:  {out.icc_min_ka:.4f} kA")
                        st.write(f"t_cc:     {out.thermal_icc_time_s:.4f} s")
                        st.write(f"Iz tabla: {out.iz_amperes:.1f} A")
                        st.write(f"Iz real:  {out.iz_corrected:.2f} A")

                if out.warnings:
                    with st.expander(
                        f"⚠️ {len(out.warnings)} Aviso(s)"
                    ):
                        for w in out.warnings:
                            st.warning(w)

                st.session_state["results"].append((inp,out))
                st.success("✅ Guardado en Resumen")

    with tab2:
        res = st.session_state.get("results",[])
        if not res:
            st.info("Calcula circuitos para ver el resumen.")
        else:
            rows = []
            for i,o in res:
                e = bool(o.errors)
                rows.append({
                    "Ref":     i.circuit_ref,
                    "P(kW)":  f"{i.power_kw:.2f}",
                    "L(m)":   f"{i.length_m:.0f}",
                    "Ib(A)":  f"{o.ib_amperes:.2f}" if not e else "-",
                    "S_T":    f"{o.section_thermal_mm2:.0f}" if not e else "-",
                    "S_ΔU":   f"{o.section_vdrop_mm2:.0f}" if not e else "-",
                    "S_FINAL":f"{o.section_phase_mm2:.0f}" if not e else "-",
                    "S_N":    f"{o.section_neutral_mm2:.0f}" if not e else "-",
                    "S_PE":   f"{o.section_pe_mm2:.0f}" if not e else "-",
                    "Iz(A)":  f"{o.iz_corrected:.1f}" if not e else "-",
                    "In(A)":  f"{o.in_protection_amperes:.0f}" if not e else "-",
                    "ΔU(%)":  f"{o.voltage_drop_pct:.2f}" if not e else "-",
                    "Icc_fin":f"{o.icc_end_ka:.3f}" if not e else "-",
                    "✓Ib≤In": "✅" if o.check_ib_lt_in else "❌",
                    "✓In≤Iz": "✅" if o.check_in_lt_iz else "❌",
                    "✓ΔU":    "✅" if o.check_vdrop_ok else "❌",
                    "✓PdC":   "✅" if o.check_icc_breaking else "❌",
                    "✓t_cc":  "✅" if o.check_thermal_icc else "❌",
                    "ESTADO": "✅OK" if o.overall_ok else
                              ("⚠️ERR" if o.errors else "❌NOK"),
                })
            df = pd.DataFrame(rows)
            st.dataframe(df,use_container_width=True,
                         hide_index=True,height=400)
            buf = io.BytesIO()
            with pd.ExcelWriter(buf,engine="openpyxl") as w:
                df.to_excel(w,index=False,
                            sheet_name="Circuitos")
            st.download_button(
                "📥 Exportar Excel",
                data=buf.getvalue(),
                file_name="calculo_electrico.xlsx",
                mime="application/vnd.openxmlformats-"
                     "officedocument.spreadsheetml.sheet"
            )
            if st.button("🗑️ Limpiar"):
                st.session_state["results"] = []
                st.rerun()

    with tab3:
        st.markdown("### 📚 Tablas UNE-HD 60364-5-52")
        s1,s2,s3 = st.tabs([
            "Capacidades","Temperatura","Agrupamiento"
        ])
        with s1:
            f1,f2,f3 = st.columns(3)
            mf  = f1.selectbox("Método",
                               ["A1","B1","C","E"],key="m")
            if_ = f2.selectbox("Aislamiento",
                               ["XLPE","PVC"],key="i")
            nf  = f3.selectbox("Conductores",[2,3],key="n",
                format_func=lambda x:
                f"{x} conductores cargados"
            )
            secs = get_sections(mf,if_,nf)
            st.dataframe(
                pd.DataFrame(
                    secs,
                    columns=["Sección(mm²)","Imax(A)"]
                ),
                use_container_width=True,hide_index=True
            )
        with s2:
            it = st.selectbox("Aislamiento",
                              ["XLPE","PVC"],key="t")
            st.dataframe(
                pd.DataFrame([
                    {"T°amb(°C)":t,"kt":get_kt(it,t)}
                    for t in range(10,76,5)
                ]),
                use_container_width=True,hide_index=True
            )
        with s3:
            ar = st.selectbox("Disposición",
                ["touching","spaced"],key="a",
                format_func=lambda x:
                "En contacto" if x=="touching"
                else "Espaciados"
            )
            st.dataframe(
                pd.DataFrame([
                    {"N°":n,"kg":get_kg(n,ar)}
                    for n in [1,2,3,4,5,6,7,8,9,12,16,20]
                ]),
                use_container_width=True,hide_index=True
            )

if __name__ == "__main__":
    main()
