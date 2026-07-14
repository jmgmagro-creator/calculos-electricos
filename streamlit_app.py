import streamlit as st
import pandas as pd
import math

# ==========================================
# 1. BASE DE DATOS NORMATIVA (UNE-HD 60364)
# ==========================================
# Catalogo: (Sección, Iz Base en Bandeja Perforada XLPE)
CABLES_IZ = [
    (1.5, 22), (2.5, 30), (4.0, 40), (6.0, 54), (10.0, 75), 
    (16.0, 100), (25.0, 127), (35.0, 158), (50.0, 192), (70.0, 246)
]
PROTECCIONES_IN = [10, 16, 20, 25, 32, 40, 50, 63, 80, 100, 125, 160, 200]

# ==========================================
# 2. MOTOR DE CÁLCULO (Clon exacto de tu Excel)
# ==========================================
def calcular_linea(row):
    # Variables de entrada
    potencia_kw = row['Potencia Instalada (kW)']
    rendimiento = row['Rendimiento (η)']
    tension = row['Tensión (V)']
    cos_phi = row['cos φ']
    longitud = row['Longitud (m)']
    kt = row['Factor Temp. (kt)']
    max_cdt = row['Límite C.D.T (%)']
    
    # --- PASO 1: Corriente Nominal (Ib) ---
    potencia_elec_w = (potencia_kw / rendimiento) * 1000
    Ib = potencia_elec_w / (math.sqrt(3) * tension * cos_phi)
    
    # --- PASO 2: Corriente de Diseño (Para caída de tensión en motores x1.25) ---
    # Tu Excel multiplica 35.28 * 1.25 = 44.10 A
    I_design = Ib * 1.25 
    
    # --- PASO 3: Calibre de Protección (In) ---
    In = next((calibre for calibre in PROTECCIONES_IN if calibre >= Ib), None)
    if In is None: return pd.Series([Ib, I_design, "Error", "Error", "Error", "Error", "Error"])

    # --- PASO 4: CRITERIO TÉRMICO (Aquí es donde la app encuentra el 6 mm2) ---
    seccion_termica = None
    iz_termica_real = None
    for sec, iz_base in CABLES_IZ:
        iz_corregida = iz_base * kt
        if iz_corregida >= In:  # 49.14 >= 40 (¡Cumple!)
            seccion_termica = sec
            iz_termica_real = iz_corregida
            break
            
    if seccion_termica is None: return pd.Series([Ib, I_design, In, "> 70", "> 70", "Error", "Error"])

    # --- PASO 5: CRITERIO CAÍDA DE TENSIÓN (Itera buscando el 10 mm2) ---
    rho = 0.0225 # Resistividad Cu
    sin_phi = math.sqrt(1 - cos_phi**2)
    
    seccion_final = seccion_termica
    cdt_volts = 0
    cdt_porc = 0
    
    for sec, iz_base in CABLES_IZ:
        if sec < seccion_termica: continue # Ignoramos los cables que no cumplen térmicamente
        
        # Ojo: la reactancia X=0.08 la aplicamos para todos en tu Excel
        X = 0.08 / 1000 
        
        # Fórmula con la I_design (44.10 A)
        delta_u = math.sqrt(3) * I_design * longitud * ((rho / sec) * cos_phi + X * sin_phi)
        porcentaje = (delta_u / tension) * 100
        
        if porcentaje <= max_cdt:
            seccion_final = sec
            cdt_volts = delta_u
            cdt_porc = porcentaje
            break

    # --- Salida de Datos ---
    estado = "OK" if cdt_porc <= max_cdt else "ERROR"
    return pd.Series([
        round(Ib, 2), 
        round(I_design, 2), 
        In, 
        seccion_termica,   # Mostrará 6 mm2
        seccion_final,     # Mostrará 10 mm2
        round(cdt_porc, 2), 
        estado
    ])

# ==========================================
# 3. INTERFAZ GRÁFICA (Streamlit)
# ==========================================
def main():
    st.set_page_config(page_title="Cálculo Motor", layout="wide")
    st.title("⚡ Verificador de Secciones (Térmica vs C.D.T)")
    st.markdown("Este motor separa la comprobación por calentamiento (6mm²) de la comprobación por longitud (10mm²).")
    
    # Cargar datos por defecto de tu captura
    if 'df_input' not in st.session_state:
        st.session_state.df_input = pd.DataFrame({
            'ID Circuito': ['Bomba Pasteurización (Tu Captura)'],
            'Potencia Instalada (kW)': [18.5],
            'Rendimiento (η)': [0.86],
            'Tensión (V)': [400],
            'cos φ': [0.88],
            'Longitud (m)': [100],
            'Factor Temp. (kt)': [0.91],
            'Límite C.D.T (%)': [5.0]
        })
        
    st.subheader("1. Datos del Circuito")
    df_edited = st.data_editor(st.session_state.df_input, use_container_width=True)
    
    if st.button("🚀 Calcular Secciones (Iterar)"):
        res = df_edited.copy()
        
        # Ejecutar el cálculo
        columnas = ['Ib (A)', 'I_design (A)', 'In (A)', 'Sección TÉRMICA (mm2)', 'Sección FINAL (mm2)', 'C.D.T Final (%)', 'Estado']
        res[columnas] = res.apply(calcular_linea, axis=1)
        
        st.subheader("2. Resultados Desglosados")
        
        # Pintar la tabla
        def colorear(val):
            if val == 'OK': return 'background-color: #28a745; color: white'
            elif val == 'ERROR': return 'background-color: #dc3545; color: white'
            return ''
            
        st.dataframe(res.style.map(colorear), use_container_width=True)
        
        # Mensaje de confirmación técnica
        sec_term = res['Sección TÉRMICA (mm2)'].iloc[0]
        sec_fin = res['Sección FINAL (mm2)'].iloc[0]
        st.success(f"✅ **Análisis correcto:** La aplicación detecta que la sección de **{sec_term} mm² cumple térmicamente** ($I_z$ > $I_n$). Sin embargo, para cumplir con el límite del 5% de caída de tensión a 100 metros, itera y sobredimensiona el cable hasta **{sec_fin} mm²**.")

if __name__ == "__main__":
    main()
