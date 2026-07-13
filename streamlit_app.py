# ================================================================
# ELECTRICAL CALC PRO - VERSIÓN MONOLÍTICA
# Pega este archivo como app.py y ejecuta:
# streamlit run app.py
# ================================================================

import streamlit as st
import pandas as pd
import sqlite3
import math
import json
import io
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from contextlib import contextmanager

# ── CONFIGURACIÓN ────────────────────────────────────────────
st.set_page_config(
    page_title="⚡ ElecCalc UNE-HD 60364",
    page_icon="⚡",
    layout="wide"
)

# ── CONSTANTES ───────────────────────────────────────────────
SQRT3            = math.sqrt(3)
RESISTIVITY_CU   = 0.01724
RESISTIVITY_AL   = 0.02830
TEMP_COEFF_CU    = 0.00393
TEMP_COEFF_AL    = 0.00403
DB_PATH          = "eleccalc.db"

# ── ENUMS ────────────────────────────────────────────────────
class CircuitType(str, Enum):
    THREE_PHASE  = "THREE_PHASE"
    SINGLE_PHASE = "SINGLE_PHASE"

class LoadType(str, Enum):
    MOTOR     = "MOTOR"
    LIGHTING  = "LIGHTING"
    HEATING   = "HEATING"
    MIXED     = "MIXED"

class InsulationType(str, Enum):
    PVC  = "PVC"
    XLPE = "XLPE"

class ConductorMaterial(str, Enum):
    Cu = "Cu"
    Al = "Al"

class InstallationMethod(str, Enum):
    A1 = "A1"
    B1 = "B1"
    C  = "C"
    E  = "E"

class ProtectionType(str, Enum):
    MCB  = "MCB"
    MCCB = "MCCB"
    FUSE = "FUSE"

# ── DATACLASSES ──────────────────────────────────────────────
@dataclass
class CircuitInput:
    circuit_ref:          str
    description:          str            = ""
    circuit_type:         str            = "THREE_PHASE"
    load_type:            str            = "MOTOR"
    power_kw:             float          = 0.0
    power_factor:         float          = 0.85
    efficiency:           float          = 0.92
    demand_factor:        float          = 1.0
    simultaneity_factor:  float          = 1.0
    voltage_v:            float          = 400.0
    conductor_material:   str            = "Cu"
    insulation_type:      str            = "XLPE"
    installation_method:  str            = "B1"
    length_m:             float          = 10.0
    ambient_temp_c:       int            = 40
    num_grouped_circuits: int            = 1
    grouping_arrangement: str            = "touching"
    icc_origin_ka:        float          = 10.0
    protection_type:      str            = "MCB"
    protection_curve:     str            = "C"

@dataclass
class CircuitOutput:
    ib_amperes:            float = 0.0
    kt:                    float = 1.0
    kg:                    float = 1.0
    total_correction:      float = 1.0
    ib_corrected:          float = 0.0
    section_phase_mm2:     float = 0.0
    section_neutral_mm2:   float = 0.0
    section_pe_mm2:        float = 0.0
    iz_amperes:            float = 0.0
    iz_corrected:          float = 0.0
    in_protection_amperes: float = 0.0
    voltage_drop_v:        float = 0.0
    voltage_drop_pct:      float = 0.0
    icc_end_ka:            float = 0.0
    check_ib_lt_in:        bool  = False
    check_in_lt_iz:        bool  = False
    check_vdrop_ok:        bool  = False
    check_icc_breaking:    bool  = False
    overall_ok:            bool  = False
    warnings:              list  = field(default_factory=list)
    errors:                list  = field(default_factory=list)

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
    """Inicializa BD con datos normativos"""
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS current_capacity_cu (
            installation_method TEXT,
            insulation_type     TEXT,
            num_conductors      INTEGER,
            section_mm2         REAL,
            imax_amperes        REAL,
            UNIQUE(installation_method, insulation_type,
                   num_conductors, section_mm2)
        );
        CREATE TABLE IF NOT EXISTS correction_temp (
            insulation_type TEXT,
            ambient_temp_c  INTEGER,
            factor          REAL,
            UNIQUE(insulation_type, ambient_temp_c)
        );
        CREATE TABLE IF NOT EXISTS correction_group (
            num_circuits INTEGER,
            arrangement  TEXT,
            factor       REAL,
            UNIQUE(num_circuits, arrangement)
        );
        CREATE TABLE IF NOT EXISTS conductor_impedance (
            material    TEXT,
            section_mm2 REAL,
            r_ohm_km    REAL,
            x_ohm_km    REAL,
            UNIQUE(material, section_mm2)
        );
        """)

        # ── Capacidades de corriente Cu ─────────────────────
        capacity_data = [
            # (method, insulation, n_cond, section, Imax)
            # A1 - PVC
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
            # A1 - XLPE
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
            # B1 - PVC
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
            # B1 - XLPE
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
            # C - PVC
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
            # C - XLPE
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
            # E - XLPE
            ("E","XLPE",2,1.5,24.0),("E","XLPE",2,2.5,33.0),
            ("E","XLPE",2,4.0,45.0),("E","XLPE",2,6.0,58.0),
            ("E","XLPE",2,10.0,80.0),("E","XLPE",2,16.0,107.0),
            ("E","XLPE",2,25.0,142.0),("E","XLPE",2,35.0,175.0),
            ("E","XLPE",2,50.0,214.0),("E","XLPE",2,70.0,273.0),
            ("E","XLPE",2,95.0,332.0),("E","XLPE",2,120.0,386.0),
            ("E","XLPE",3,1.5,22.0),("E","XLPE",3,2.5,30.0),
            ("E","XLPE",3,4.0,40.0),("E","XLPE",3,6.0,52.0),
            ("E","XLPE",3,10.0,71.0),("E","XLPE",3,16.0,96.0),
            ("E","XLPE",3,25.0,127.0),("E","XLPE",3,35.0,157.0),
            ("E","XLPE",3,50.0,192.0),("E","XLPE",3,70.0,245.0),
            ("E","XLPE",3,95.0,298.0),("E","XLPE",3,120.0,346.0),
            ("E","PVC",2,1.5,19.5),("E","PVC",2,2.5,27.0),
            ("E","PVC",2,4.0,36.0),("E","PVC",2,6.0,46.0),
            ("E","PVC",2,10.0,63.0),("E","PVC",2,16.0,85.0),
            ("E","PVC",2,25.0,112.0),("E","PVC",2,35.0,138.0),
            ("E","PVC",2,50.0,168.0),("E","PVC",2,70.0,213.0),
            ("E","PVC",3,1.5,17.5),("E","PVC",3,2.5,24.0),
            ("E","PVC",3,4.0,32.0),("E","PVC",3,6.0,41.0),
            ("E","PVC",3,10.0,57.0),("E","PVC",3,16.0,76.0),
            ("E","PVC",3,25.0,101.0),("E","PVC",3,35.0,125.0),
            ("E","PVC",3,50.0,151.0),("E","PVC",3,70.0,192.0),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO current_capacity_cu "
            "(installation_method,insulation_type,num_conductors,"
            "section_mm2,imax_amperes) VALUES(?,?,?,?,?)",
            capacity_data
        )

        # ── Factores de temperatura ─────────────────────────
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
            "(insulation_type,ambient_temp_c,factor) VALUES(?,?,?)",
            temp_data
        )

        # ── Factores de agrupamiento ────────────────────────
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
            "(num_circuits,arrangement,factor) VALUES(?,?,?)",
            group_data
        )

        # ── Impedancias de conductor ────────────────────────
        impedance_data = [
            ("Cu",1.5,12.10,0.115),("Cu",2.5,7.41,0.110),
            ("Cu",4.0,4.61,0.107), ("Cu",6.0,3.08,0.102),
            ("Cu",10.0,1.83,0.096),("Cu",16.0,1.15,0.090),
            ("Cu",25.0,0.727,0.086),("Cu",35.0,0.524,0.083),
            ("Cu",50.0,0.387,0.080),("Cu",70.0,0.268,0.077),
            ("Cu",95.0,0.193,0.075),("Cu",120.0,0.153,0.074),
            ("Cu",150.0,0.124,0.073),("Cu",185.0,0.0991,0.072),
            ("Cu",240.0,0.0754,0.071),
            ("Al",16.0,1.91,0.090),("Al",25.0,1.20,0.086),
            ("Al",35.0,0.868,0.083),("Al",50.0,0.641,0.080),
            ("Al",70.0,0.443,0.077),("Al",95.0,0.320,0.075),
            ("Al",120.0,0.253,0.074),("Al",150.0,0.206,0.073),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO conductor_impedance "
            "(material,section_mm2,r_ohm_km,x_ohm_km) VALUES(?,?,?,?)",
            impedance_data
        )

    return True

# ── TABLAS DE REFERENCIA ──────────────────────────────────────
PROTECTION_RATINGS = {
    "MCB":  [6,10,13,16,20,25,32,40,50,63],
    "MCCB": [16,20,25,32,40,50,63,80,100,125,
             160,200,250,315,400,500,630],
    "FUSE": [2,4,6,10,16,20,25,32,40,50,
             63,80,100,125,160,200,250,315,400],
}
VDROP_LIMITS = {
    "MOTOR":5.0,"LIGHTING":3.0,"HEATING":3.0,"MIXED":3.0
}
BREAKING_CAPACITY = {"MCB":6.0,"MCCB":25.0,"FUSE":80.0}

# ── FUNCIONES DE CONSULTA BD ──────────────────────────────────
def get_sections(method, insulation, n_cond, material="Cu"):
    table = "current_capacity_cu"
    with get_db() as conn:
        rows = conn.execute(
            f"SELECT section_mm2, imax_amperes FROM {table} "
            f"WHERE installation_method=? AND insulation_type=? "
            f"AND num_conductors=? ORDER BY section_mm2 ASC",
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
        # Interpolación
        rows = conn.execute(
            "SELECT ambient_temp_c, factor FROM correction_temp "
            "WHERE insulation_type=? ORDER BY ambient_temp_c",
            (insulation,)
        ).fetchall()
    pts = [(r["ambient_temp_c"], r["factor"]) for r in rows]
    for i in range(len(pts)-1):
        t0,f0 = pts[i]; t1,f1 = pts[i+1]
        if t0 <= temp <= t1:
            return round(f0+(f1-f0)*(temp-t0)/(t1-t0), 4)
    return pts[-1][1] if temp > pts[-1][0] else pts[0][1]

def get_kg(n_circuits, arrangement):
    with get_db() as conn:
        row = conn.execute(
            "SELECT factor FROM correction_group "
            "WHERE num_circuits=? AND arrangement=?",
            (n_circuits, arrangement)
        ).fetchone()
        if row:
            return row["factor"]
        row = conn.execute(
            "SELECT factor FROM correction_group "
            "WHERE num_circuits<=? AND arrangement=? "
            "ORDER BY num_circuits DESC LIMIT 1",
            (n_circuits, arrangement)
        ).fetchone()
    return row["factor"] if row else 1.0

def get_impedance(material, section):
    with get_db() as conn:
        row = conn.execute(
            "SELECT r_ohm_km, x_ohm_km FROM conductor_impedance "
            "WHERE material=? AND section_mm2=?",
            (material, section)
        ).fetchone()
    return (row["r_ohm_km"], row["x_ohm_km"]) if row else (
        (RESISTIVITY_CU/section)*1000, 0.08
    )

def normalize_section(s, material="Cu"):
    std = [1.5,2.5,4,6,10,16,25,35,50,70,95,120,150,185,240,300]
    if material == "Al":
        std = [x for x in std if x >= 16]
    return next((x for x in std if x >= s), std[-1])

# ── MOTOR DE CÁLCULO ──────────────────────────────────────────
def calculate(inp: CircuitInput) -> CircuitOutput:
    out = CircuitOutput()

    # PASO 1: Corriente de diseño
    p_w = inp.power_kw * 1000 * inp.demand_factor * inp.simultaneity_factor
    denom = inp.power_factor * inp.efficiency
    if denom <= 0:
        out.errors.append("cosφ · η debe ser > 0")
        return out

    if inp.circuit_type == "THREE_PHASE":
        ib = p_w / (SQRT3 * inp.voltage_v * denom)
    else:
        ib = p_w / (inp.voltage_v * denom)

    if inp.load_type == "MOTOR":
        ib *= 1.25   # REBT ITC-BT-47
        out.warnings.append("Motor: Ib × 1.25 aplicado (REBT ITC-BT-47)")

    out.ib_amperes = round(ib, 3)

    # PASO 2: Factores de corrección
    out.kt = get_kt(inp.insulation_type, inp.ambient_temp_c)
    out.kg = get_kg(inp.num_grouped_circuits, inp.grouping_arrangement)
    out.total_correction = round(out.kt * out.kg, 4)

    if out.total_correction <= 0:
        out.errors.append("Factor de corrección = 0")
        return out

    # PASO 3: Corriente ficticia
    out.ib_corrected = round(out.ib_amperes / out.total_correction, 3)

    # PASO 4: Búsqueda de sección mínima
    n_cond = 2 if inp.circuit_type == "SINGLE_PHASE" else 3
    sections = get_sections(
        inp.installation_method,
        inp.insulation_type,
        n_cond,
        inp.conductor_material
    )

    found = None
    for s, iz in sections:
        if iz >= out.ib_corrected:
            found = (s, iz)
            break

    if found is None:
        out.errors.append(
            f"Sin sección suficiente para Ib'={out.ib_corrected:.1f}A "
            f"[{inp.installation_method}/{inp.insulation_type}]"
        )
        return out

    out.section_phase_mm2 = found[0]
    out.iz_amperes        = found[1]
    out.iz_corrected      = round(out.iz_amperes * out.total_correction, 2)

    # Sección mínima REBT
    s_min = 1.5 if inp.load_type == "LIGHTING" else 2.5
    out.section_phase_mm2 = max(out.section_phase_mm2, s_min)

    # Sección neutro
    if inp.circuit_type == "SINGLE_PHASE":
        out.section_neutral_mm2 = out.section_phase_mm2
    else:
        thr = 16 if inp.conductor_material == "Cu" else 25
        out.section_neutral_mm2 = (
            out.section_phase_mm2 if out.section_phase_mm2 <= thr
            else normalize_section(out.section_phase_mm2 / 2,
                                   inp.conductor_material)
        )

    # Sección PE (Tabla 54.2)
    s = out.section_phase_mm2
    if   s <= 16: out.section_pe_mm2 = s
    elif s <= 35: out.section_pe_mm2 = 16.0
    else:         out.section_pe_mm2 = normalize_section(s/2)

    # PASO 5: Protección normalizada
    ratings = PROTECTION_RATINGS.get(inp.protection_type, [])
    out.in_protection_amperes = float(
        next((r for r in sorted(ratings) if r >= out.ib_amperes),
             sorted(ratings)[-1] if ratings else out.ib_amperes)
    )

    # PASO 6: Checks de corriente
    out.check_ib_lt_in = (out.ib_amperes <= out.in_protection_amperes)
    out.check_in_lt_iz = (out.in_protection_amperes <= out.iz_corrected)

    if not out.check_ib_lt_in:
        out.warnings.append(
            f"❌ Ib={out.ib_amperes:.1f}A > In={out.in_protection_amperes:.0f}A"
        )
    if not out.check_in_lt_iz:
        out.warnings.append(
            f"❌ In={out.in_protection_amperes:.0f}A > Iz={out.iz_corrected:.1f}A"
        )

    # PASO 7: Caída de tensión
    r_km, x_km = get_impedance(inp.conductor_material, out.section_phase_mm2)
    t_max  = 70.0 if inp.insulation_type == "PVC" else 90.0
    alpha  = TEMP_COEFF_CU if inp.conductor_material == "Cu" else TEMP_COEFF_AL
    r_km   = r_km * (1 + alpha * (t_max - 20))
    r_m    = r_km / 1000.0
    x_m    = x_km / 1000.0
    cosp   = inp.power_factor
    sinp   = math.sqrt(max(0, 1 - cosp**2))
    factor = SQRT3 if inp.circuit_type == "THREE_PHASE" else 2.0

    if out.section_phase_mm2 < 50:
        delta_u = factor * out.ib_amperes * inp.length_m * r_m
    else:
        delta_u = factor * out.ib_amperes * inp.length_m * (
            r_m * cosp + x_m * sinp
        )

    out.voltage_drop_v   = round(delta_u, 4)
    out.voltage_drop_pct = round((delta_u / inp.voltage_v) * 100, 4)

    limit_pct = VDROP_LIMITS.get(inp.load_type, 3.0)
    out.check_vdrop_ok = (out.voltage_drop_pct <= limit_pct)
    if not out.check_vdrop_ok:
        out.warnings.append(
            f"❌ ΔU={out.voltage_drop_pct:.2f}% > límite {limit_pct}%"
        )

    # PASO 8: Corriente de cortocircuito en fin de línea
    if inp.circuit_type == "THREE_PHASE":
        z_orig = inp.voltage_v / (SQRT3 * inp.icc_origin_ka * 1000)
    else:
        z_orig = inp.voltage_v / (2 * inp.icc_origin_ka * 1000)

    r_km0, x_km0 = get_impedance(inp.conductor_material, out.section_phase_mm2)
    factor_z = 1 if inp.circuit_type == "THREE_PHASE" else 2
    z_r = z_orig + factor_z * (r_km0 / 1000) * inp.length_m
    z_x = factor_z * (x_km0 / 1000) * inp.length_m
    z_t = math.sqrt(z_r**2 + z_x**2)

    if inp.circuit_type == "THREE_PHASE":
        out.icc_end_ka = round((inp.voltage_v / (SQRT3 * z_t)) / 1000, 4)
    else:
        out.icc_end_ka = round((inp.voltage_v / (2 * z_t)) / 1000, 4)

    # PASO 9: Poder de corte
    pdc = BREAKING_CAPACITY.get(inp.protection_type, 6.0)
    out.check_icc_breaking = (inp.icc_origin_ka <= pdc)
    if not out.check_icc_breaking:
        out.warnings.append(
            f"❌ Icc={inp.icc_origin_ka}kA > PdC={pdc}kA"
        )

    out.overall_ok = (
        out.check_ib_lt_in and out.check_in_lt_iz and
        out.check_vdrop_ok and out.check_icc_breaking
    )

    return out

# ── INTERFAZ DE USUARIO ───────────────────────────────────────
def main():
    init_db()

    st.markdown("""
    <h1 style='color:#00b4d8;margin-bottom:0'>
        ⚡ ElecCalc Pro
    </h1>
    <p style='color:#8899aa;margin-top:0'>
        Dimensionamiento eléctrico · UNE-HD 60364-5-52 · REBT 2002
    </p>
    <hr>
    """, unsafe_allow_html=True)

    if "session_results" not in st.session_state:
        st.session_state["session_results"] = []

    tab1, tab2, tab3 = st.tabs([
        "⚡ Calcular Circuito",
        "📊 Resultados Sesión",
        "📚 Tablas Normativas"
    ])

    # ── TAB 1: FORMULARIO ─────────────────────────────────────
    with tab1:
        st.markdown("### Datos de Entrada")

        c1, c2, c3 = st.columns(3)

        with c1:
            ref = st.text_input("Referencia *", "C-001")
            desc = st.text_input("Descripción", "Motor bomba agua")
            ctype = st.selectbox(
                "Tipo Circuito",
                ["THREE_PHASE","SINGLE_PHASE"],
                format_func=lambda x:
                    "🔴 Trifásico" if x=="THREE_PHASE" else "⚫ Monofásico"
            )
            ltype = st.selectbox(
                "Tipo Carga",
                ["MOTOR","LIGHTING","HEATING","MIXED"],
                format_func=lambda x: {
                    "MOTOR":"⚙️ Motor","LIGHTING":"💡 Alumbrado",
                    "HEATING":"🔥 Calefacción","MIXED":"🔀 Mixta"
                }[x]
            )

        with c2:
            p_kw  = st.number_input("Potencia (kW)*",
                                     0.01, 5000.0, 7.5, 0.5)
            cosphi = st.slider("cosφ", 0.5, 1.0, 0.85, 0.01)
            eta    = st.slider("η Rendimiento", 0.5, 1.0, 0.92, 0.01)
            u_v    = st.selectbox("Tensión (V)",
                                   [230.0, 400.0, 690.0], index=1)
            fd     = st.number_input("Factor Demanda", 0.1, 1.0, 1.0, 0.05)

        with c3:
            mat  = st.selectbox("Material", ["Cu","Al"])
            ins  = st.selectbox("Aislamiento",
                                 ["XLPE","PVC"], index=0)
            meth = st.selectbox(
                "Método Instalación",
                ["A1","B1","C","E"],
                index=1,
                format_func=lambda x: {
                    "A1":"A1-Empotrado aislante",
                    "B1":"B1-Tubo en pared",
                    "C":"C-Sobre pared",
                    "E":"E-Bandeja al aire"
                }[x]
            )
            lm   = st.number_input("Longitud (m)*",
                                    0.1, 5000.0, 25.0, 1.0)
            temp = st.number_input("T ambiente (°C)",
                                    10, 75, 40, 5)
            ngr  = st.number_input("Nº Agrupados",
                                    1, 30, 1, 1)
            arr  = st.selectbox("Disposición",
                                 ["touching","spaced"],
                                 format_func=lambda x:
                                 "En contacto" if x=="touching"
                                 else "Espaciados")

        st.markdown("---")
        pc1, pc2 = st.columns(2)
        with pc1:
            prot = st.selectbox("Protección",
                                 ["MCB","MCCB","FUSE"],
                                 format_func=lambda x: {
                                     "MCB":"MCB-Magnetotérmico",
                                     "MCCB":"MCCB-Caja Moldeada",
                                     "FUSE":"FUSE-Fusible"
                                 }[x])
        with pc2:
            icc_orig = st.number_input(
                "Icc cabecera (kA)", 0.1, 150.0, 10.0, 0.5
            )

        if st.button("⚡ CALCULAR", type="primary",
                     use_container_width=True):

            inp = CircuitInput(
                circuit_ref=ref, description=desc,
                circuit_type=ctype, load_type=ltype,
                power_kw=p_kw, power_factor=cosphi,
                efficiency=eta, demand_factor=fd,
                voltage_v=u_v,
                conductor_material=mat, insulation_type=ins,
                installation_method=meth, length_m=lm,
                ambient_temp_c=temp, num_grouped_circuits=ngr,
                grouping_arrangement=arr,
                icc_origin_ka=icc_orig, protection_type=prot
            )

            with st.spinner("Calculando..."):
                out = calculate(inp)

            st.markdown("---")
            st.markdown("### 📋 Resultados")

            if out.errors:
                for e in out.errors:
                    st.error(e)
            else:
                color = "#4ade80" if out.overall_ok else "#f87171"
                st.markdown(f"""
                <div style='border-left:4px solid {color};
                            padding:0.5rem 1rem;
                            background:#1a2332;
                            border-radius:4px;
                            margin-bottom:1rem'>
                    <b style='color:{color}; font-size:1.2rem'>
                        {'✅ CIRCUITO CORRECTO' if out.overall_ok
                         else '❌ CIRCUITO CON ERRORES'}
                    </b>
                </div>
                """, unsafe_allow_html=True)

                m1,m2,m3,m4,m5,m6 = st.columns(6)
                m1.metric("Ib (A)",   f"{out.ib_amperes:.2f}")
                m2.metric("In (A)",   f"{out.in_protection_amperes:.0f}")
                m3.metric("S (mm²)",  f"{out.section_phase_mm2:.1f}")
                m4.metric("Iz (A)",   f"{out.iz_corrected:.2f}")
                m5.metric("ΔU (%)",   f"{out.voltage_drop_pct:.2f}")
                m6.metric("Icc fin",  f"{out.icc_end_ka:.3f} kA")

                st.markdown("#### Verificaciones CHECK")
                checks = {
                    "Ib ≤ In": out.check_ib_lt_in,
                    "In ≤ Iz": out.check_in_lt_iz,
                    f"ΔU ≤ {VDROP_LIMITS.get(ltype,3)}%": out.check_vdrop_ok,
                    "Icc ≤ PdC": out.check_icc_breaking,
                }
                c_cols = st.columns(4)
                for i, (name, ok) in enumerate(checks.items()):
                    c_cols[i].markdown(
                        f"<div style='text-align:center;"
                        f"background:{'#1a4731' if ok else '#4a1a1a'};"
                        f"border-radius:8px;padding:0.8rem'>"
                        f"<div style='font-size:1.5rem'>"
                        f"{'✅' if ok else '❌'}</div>"
                        f"<div style='color:#aaa;font-size:0.8rem'>"
                        f"{name}</div></div>",
                        unsafe_allow_html=True
                    )

                with st.expander("📐 Detalle técnico"):
                    d1, d2, d3 = st.columns(3)
                    with d1:
                        st.write("**Factores de corrección**")
                        st.write(f"kt = {out.kt:.3f}")
                        st.write(f"kg = {out.kg:.3f}")
                        st.write(f"fc = {out.total_correction:.3f}")
                        st.write(f"Ib' = {out.ib_corrected:.2f} A")
                    with d2:
                        st.write("**Conductores**")
                        st.write(f"Fase:   {out.section_phase_mm2} mm²")
                        st.write(f"Neutro: {out.section_neutral_mm2} mm²")
                        st.write(f"PE:     {out.section_pe_mm2} mm²")
                        st.write(f"Iz tabla: {out.iz_amperes:.1f} A")
                    with d3:
                        st.write("**Caída de tensión**")
                        st.write(f"ΔU = {out.voltage_drop_v:.4f} V")
                        st.write(f"ΔU = {out.voltage_drop_pct:.3f} %")
                        st.write(f"Icc fin = {out.icc_end_ka:.4f} kA")

                if out.warnings:
                    with st.expander(
                        f"⚠️ {len(out.warnings)} Advertencia(s)"
                    ):
                        for w in out.warnings:
                            st.warning(w)

                # Guardar en sesión
                st.session_state["session_results"].append(
                    (inp, out)
                )
                st.success("✅ Resultado guardado en Resumen de Sesión")

    # ── TAB 2: RESUMEN SESIÓN ─────────────────────────────────
    with tab2:
        results = st.session_state.get("session_results", [])

        if not results:
            st.info("Calcula al menos un circuito para ver el resumen.")
        else:
            st.markdown(f"### {len(results)} Circuito(s) calculados")
            rows = []
            for i, o in results:
                if o.errors:
                    rows.append({
                        "Ref": i.circuit_ref,
                        "Desc": i.description,
                        "P(kW)": i.power_kw,
                        "L(m)": i.length_m,
                        "Ib(A)": "-","S(mm²)":"-",
                        "In(A)":"-","ΔU(%)":"-",
                        "Icc_fin(kA)":"-",
                        "✓Ib≤In":"❌","✓In≤Iz":"❌",
                        "✓ΔU":"❌","✓PdC":"❌",
                        "ESTADO":"⚠️ERROR"
                    })
                else:
                    rows.append({
                        "Ref": i.circuit_ref,
                        "Desc": i.description,
                        "P(kW)": f"{i.power_kw:.2f}",
                        "L(m)": f"{i.length_m:.1f}",
                        "Ib(A)": f"{o.ib_amperes:.2f}",
                        "S(mm²)": f"{o.section_phase_mm2:.1f}",
                        "In(A)": f"{o.in_protection_amperes:.0f}",
                        "ΔU(%)": f"{o.voltage_drop_pct:.2f}",
                        "Icc_fin(kA)": f"{o.icc_end_ka:.3f}",
                        "✓Ib≤In":"✅" if o.check_ib_lt_in else "❌",
                        "✓In≤Iz":"✅" if o.check_in_lt_iz else "❌",
                        "✓ΔU":"✅"    if o.check_vdrop_ok else "❌",
                        "✓PdC":"✅"   if o.check_icc_breaking else "❌",
                        "ESTADO":"✅OK" if o.overall_ok else "❌NOT OK"
                    })

            df = pd.DataFrame(rows)
            st.dataframe(df, use_container_width=True,
                         hide_index=True, height=400)

            # Exportar Excel
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df.to_excel(w, index=False, sheet_name="Circuitos")

            st.download_button(
                "📥 Exportar a Excel",
                data=buf.getvalue(),
                file_name="calculo_electrico.xlsx",
                mime="application/vnd.openxmlformats-officedocument"
                     ".spreadsheetml.sheet"
            )

            if st.button("🗑️ Limpiar sesión"):
                st.session_state["session_results"] = []
                st.rerun()

    # ── TAB 3: TABLAS NORMATIVAS ──────────────────────────────
    with tab3:
        st.markdown("### 📚 Tablas UNE-HD 60364-5-52")

        sub1, sub2, sub3 = st.tabs([
            "Capacidades (A)",
            "Factores Temperatura",
            "Factores Agrupamiento"
        ])

        with sub1:
            fc1, fc2, fc3 = st.columns(3)
            with fc1:
                m_f  = st.selectbox("Método",["A1","B1","C","E"])
            with fc2:
                i_f  = st.selectbox("Aislamiento",["PVC","XLPE"])
            with fc3:
                nc_f = st.selectbox("Conductores",[2,3],
                    format_func=lambda x:f"{x} cond. cargados")

            secs = get_sections(m_f, i_f, nc_f)
            if secs:
                st.dataframe(
                    pd.DataFrame(secs,
                        columns=["Sección (mm²)","Imax (A)"]),
                    use_container_width=True, hide_index=True
                )
            else:
                st.warning("Sin datos para esa combinación.")

        with sub2:
            it_f = st.selectbox("Aislamiento",["PVC","XLPE"],key="t2")
            td = [{"T_amb (°C)":t,
                   "kt":get_kt(it_f,t)}
                  for t in range(10,76,5)]
            st.dataframe(pd.DataFrame(td),
                         use_container_width=True, hide_index=True)

        with sub3:
            ar_f = st.selectbox("Disposición",
                ["touching","spaced"],
                format_func=lambda x:
                "En contacto" if x=="touching" else "Espaciados")
            gd = [{"N° circuitos":n,
                   "kg":get_kg(n,ar_f)}
                  for n in [1,2,3,4,5,6,7,8,9,12,16,20]]
            st.dataframe(pd.DataFrame(gd),
                         use_container_width=True, hide_index=True)

if __name__ == "__main__":
    main()
