import streamlit as st
import pandas as pd
import sqlite3
import math

# ==========================================
# 1. BASE DE DATOS (Mismos valores que tu Excel)
# ==========================================
def init_db():
    conn = sqlite3.connect(':memory:') # Usamos memoria para ser más rápidos
    c = conn.cursor()
    c.execute('''CREATE TABLE cables_iz (seccion REAL, iz REAL)''')
    c.execute('''CREATE TABLE protecciones_in (in_amperios REAL)''')
    
    # Iz Base de tu Excel: 6mm(54A) y 10mm(75A asumiendo 68.25/0.91)
    sample_cables = [(1.5, 22), (2.5, 30), (4.0, 40), (6.0, 54), (10.0, 75), 
                     (16.0, 100), (25.0, 127), (35.0, 158), (50.0, 192), (70.0, 246)]
    c.executemany("INSERT INTO cables_iz VALUES (?,?)", sample_cables)
    
    breakers = [(10,), (16,), (20,), (25,), (32,), (40,), (50,), (63,), (80,), (100,), (125,)]
    c.executemany("INSERT INTO protecciones_in VALUES (?)", breakers)
    conn.commit()
    return conn

# ==========================================
# 2. MOTOR DE CÁLCULO (Clon de tu Excel)
# ==========================================
def calcular_circuito(row, conn):
    # Inputs
    P_inst = row['Pot. Instalada (kW)']
    rendimiento = row['Eficiencia (η)']
    V = row['Tensión (V)']
    cos_phi = row['cos φ']
    L = row['Longitud (m)']
    ft = row['Factor Temp. (kt)']
    max_cdt = row['Max % cdt']
    is_motor = row['Es Motor (x1.25)']
    
    # 1. Corriente Estimada (Ib)
    P_elec_w = (P_inst / rendimiento) * 1000
    Ib = P_elec_w / (math.sqrt(3) * V * cos_phi)
    
    # 2. Corriente de Diseño del Cable (Idesign) - Factor 1.25 para motores
    Idesign = Ib * 1.25 if is_motor else Ib
    
    # 3. Protección (In)
    c = conn.cursor()
    c.execute("SELECT in_amperios FROM protecciones_in WHERE in_amperios >= ? ORDER BY in_amperios ASC LIMIT 1", (Ib,))
    In = c.fetchone()[0]
    
    # Extraer catálogo de cables
    c.execute("SELECT seccion, iz FROM cables_iz ORDER BY seccion ASC")
    cables = c.fetchall()
    
    # 4. FASE 1: Buscar Sección por TÉRMICA (Ib <= In <= Iz)
    seccion_termica = None
    iz_termica_final = None
    for sec_t, iz_base in cables:
        iz_corregida = iz_base * ft
        if iz_corregida >= In:
            seccion_termica = sec_t
            iz_termica_final = iz_corregida
            break
            
    if not seccion_termica:
        return pd.Series([round(Ib,2), round(Idesign,2), In, "Error", "Error", "Error", "Error", "Error"])

    # 5. FASE 2: Calcular Caída de Tensión (C.D.T.) iterando desde la sección térmica
    rho = 0.0225 # Resistividad Cu 90ºC de tu Excel
    sin_phi = math.sqrt(1 - cos_phi**2)
    
    seccion_final = None
    cdt_volts = None
    cdt_porcentaje = None
    
    for sec_c, iz_base in cables:
        if sec_c < seccion_termica:
            continue # Ni lo miramos, ya sabemos que se quema
            
        X = 0.08 / 1000 if sec_c >= 16 else 0.00008 # Ojo, en tu excel X=0.08 para todos
        # Fórmula exacta de tu Excel usando la Corriente de Diseño:
        delta_U = math.sqrt(3) * Idesign * L * ((rho / sec_c) * cos_phi + X * sin_phi)
        porcentaje = (delta_U / V) * 100
        
        if porcentaje <= max_cdt:
            seccion_final = sec_c
            cdt_volts = delta_U
            cdt_porcentaje = porcentaje
            break

    # Estado Final (Validación)
    estado = "OK" if seccion_final else "ERROR CDT"
    if not seccion_final:
        seccion_final = "> 70"
        cdt_porcentaje = "N/A"

    return pd.Series([
        round(Ib, 2), 
        round(Idesign, 2), 
        In, 
        seccion_termica, # Te mostramos la que cumple por calor (Ej. 6)
        seccion_final,   # Te mostramos la final real requerida (Ej. 10)
        round(cdt_volts, 2), 
        round(cdt_porcentaje, 2), 
        estado
    ])

# ==========================================
# 3. INTERFAZ (UI)
# ==========================================
def main():
    st.set_page_config(page_title="Motor Eléctrico", layout="wide")
    st.title("⚡ Verificador Normativo de Cables")
    st.markdown("*Cálculos ajustados exactamente a la metodología de tu plantilla Excel.*")
    
    conn = init_db()
    
    # Input inicial (Tu Bomba a 100m exactamente configurada)
    if 'input_data' not in st.session_state:
        st.session_state.input_data = pd.DataFrame({
            'Tag': ['A9 Pasteurization Pump'],
            'Pot. Instalada (kW)': [18.5], 
            'Eficiencia (η)': [0.86],
            'Tensión (V)': [400],
            'cos φ': [0.88],
            'Longitud (m)': [100],
            'Factor Temp. (kt)': [0.91],
            'Max % cdt': [5.0],
            'Es Motor (x1.25)': [True] # Este check replica tu 'Cable Design Current'
        })
    
    st.subheader("📥 Datos de Entrada")
    edited_df = st.data_editor(st.session_state.input_data, use_container_width=True)
    
    if st.button("🚀 Calcular Sección Óptima"):
        res = edited_df.copy()
        cols = ['Ib (A)', 'I_design (A)', 'In (A)', 'Sección Térmica (mm2)', 'Sección Final C.D.T (mm2)', 'ΔU (V)', 'ΔU (%)', 'ESTADO']
        res[cols] = res.apply(lambda row: calcular_circuito(row, conn), axis=1)
        
        st.subheader("📤 Resultados Desglosados")
        
        # Damos formato visual para diferenciar térmica y cdt
        def style_df(row):
            color = 'background-color: #d4edda' if row['ESTADO'] == 'OK' else 'background-color: #f8d7da'
            return [color] * len(row)
            
        st.dataframe(res.style.apply(style_df, axis=1), use_container_width=True)
        
        # Explicación para el ingeniero
        st.info(f"💡 **Análisis de la bomba:** El cable de **{res['Sección Térmica (mm2)'][0]} mm²** aguanta perfectamente el calor (Térmica OK). Sin embargo, debido a la longitud, la caída de tensión nos obliga a subir a **{res['Sección Final C.D.T (mm2)'][0]} mm²** para no superar el 5%.")

if __name__ == "__main__":
    main()
