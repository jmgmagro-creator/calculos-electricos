# ⚡ Cálculo de Secciones BT — UNE-HD 60364

Aplicación web para el dimensionado de cables de baja tensión conforme a la normativa española (REBT, ITC-BT-19, ITC-BT-47) y europea (UNE-HD 60364-5-52).

## Funcionalidades

- **Criterio térmico**: selección de sección por intensidad admisible (Iz) con tablas completas UNE-HD 60364-5-52
- **Criterio de caída de tensión**: iteración automática hasta cumplir el límite de ΔU
- **Verificación de cortocircuito**: Ik mín en carga, poder de corte, tiempo térmico admisible
- **Veredicto final** global con todas las comprobaciones

### Tablas normativas incluidas

| Parámetro | Opciones |
|---|---|
| Método de instalación | B1, C, E, F |
| Material conductor | Cu, Al |
| Aislamiento | XLPE, PVC |
| Conductores cargados | 2 (monofásico), 3 (trifásico) |

### Factores de corrección

- **kt** (temperatura): calculado automáticamente a partir de T ambiente y tipo de aislamiento
- **kg** (agrupamiento): entrada manual

### Parámetros de cálculo

- Resistividad ρ según material y aislamiento (Cu/XLPE: 0,0225 Ω·mm²/m)
- Reactancia X = 0,08 Ω/km
- Factor k de cortocircuito (Cu/XLPE: 143, Cu/PVC: 115, Al/XLPE: 94, Al/PVC: 76)
- Calibres normalizados de protección: 6 a 630 A
- Secciones normalizadas: 1,5 a 300 mm²

## Uso

### Opción 1 — Abrir directamente

Abrir `index.html` en cualquier navegador. No requiere servidor ni dependencias.

### Opción 2 — PWA en móvil

1. Abrir en Chrome
2. Menú ⋮ → "Añadir a pantalla de inicio"

### Opción 3 — Deploy en Netlify

1. Arrastrar la carpeta al panel de [Netlify Drop](https://app.netlify.com/drop)

## Estructura

```
index.html    ← Aplicación completa (HTML + CSS + JS autocontenido)
README.md     ← Este archivo
```

## Normativa de referencia

- REBT (RD 842/2002) — Reglamento Electrotécnico para Baja Tensión
- ITC-BT-07 — Redes subterráneas de distribución
- ITC-BT-19 — Instalaciones interiores: prescripciones generales
- ITC-BT-47 — Instalación de receptores: motores
- UNE-HD 60364-5-52 — Selección e instalación de equipos eléctricos: canalizaciones

## Licencia

Uso interno.
