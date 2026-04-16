import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import plotly.express as px
from datetime import date, timedelta
from processing import init_gee, get_analysis_data, get_time_series

# ─────────────────────────────────────────────
# CONFIG PAGE
# ─────────────────────────────────────────────
st.set_page_config(page_title="Agri-Sentinel Pro", layout="wide", page_icon="🌿")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600&family=Space+Grotesk:wght@700&display=swap');
html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }
div[data-testid="stMetricValue"] { font-family: 'Space Grotesk', sans-serif; font-size: 2rem; color: #2e7d32; }
.diag-box { border-radius: 10px; padding: 14px 18px; margin: 10px 0; font-weight: 500; }
.diag-ok    { background:#e8f5e9; border-left:4px solid #2e7d32; color:#1b5e20; }
.diag-warn  { background:#fff8e1; border-left:4px solid #f9a825; color:#e65100; }
.diag-alert { background:#ffebee; border-left:4px solid #c62828; color:#b71c1c; }
</style>
""", unsafe_allow_html=True)

if not init_gee():
    st.stop()

# --- MÉMOIRE DE L'APPLICATION ---
if 'analyse_lancee' not in st.session_state:
    st.session_state.analyse_lancee = False

# ═══════════════════════════════════════════
# SIDEBAR : SAISIE & DATES
# ═══════════════════════════════════════════
with st.sidebar:
    st.markdown("## 🌿 Agri-Sentinel Pro")
    st.markdown("---")
    
    # Choix du mode (Démo vs Agriculteur)
    mode_saisie = st.selectbox(
        "📍 Sélectionner ma parcelle", 
        ["Parcelle Test (Démonstration)", "Saisir une nouvelle parcelle"]
    )
    
    if mode_saisie == "Parcelle Test (Démonstration)":
        st.info("Surface très réduite pour analyse de précision.")
        # Coordonnées par défaut de la parcelle test
        lat_up = 33.76416
        lon_left = -6.24217
        lat_down = 33.75990
        lon_right = -6.23253
    else:
        st.write("Entrez les coordonnées de votre champ :")
        lat_up = st.number_input("Latitude Nord", value=33.76416, format="%.5f")
        lon_left = st.number_input("Longitude Ouest", value=-6.24217, format="%.5f")
        lat_down = st.number_input("Latitude Sud", value=33.75990, format="%.5f")
        lon_right = st.number_input("Longitude Est", value=-6.23253, format="%.5f")

    roi_coords = [lon_left, lat_down, lon_right, lat_up]
    
    st.markdown("---")
    
    st.markdown("### 📅 Période d'analyse")
    date_analyse = st.date_input("Date cible (pour la carte)", value=date.today())
    date_debut = st.date_input("Début de l'historique (pour le graphique)", value=date.today() - timedelta(days=90))
    
    # Quand on clique, on change la mémoire à Vrai (True)
    if st.button("🚀 Lancer le traitement", use_container_width=True, type="primary"):
        st.session_state.analyse_lancee = True

# ═══════════════════════════════════════════
# PAGE PRINCIPALE
# ═══════════════════════════════════════════
st.title("🚜 Suivi et Diagnostic de ma parcelle")

# On vérifie la mémoire pour afficher les résultats
if st.session_state.analyse_lancee:
    with st.spinner("🛰️ Analyse satellite en cours..."):
        
        # --- PARTIE 1 : LA CARTE ET LE DIAGNOSTIC ---
        res = get_analysis_data(roi_coords, str(date_analyse))
        
        if "error" in res:
            st.error(res["error"])
        else:
            v = res['vigueur_avg']
            e = res['eau_avg']
            
            # Affichage des scores
            c1, c2, c3 = st.columns(3)
            c1.metric("Date de l'image satellite", res['date_capture'])
            c2.metric("Vigueur (NDVI)", f"{v:.2f}")
            c3.metric("Humidité (NDWI)", f"{e:.2f}")

            # Diagnostic intelligent
            if v < 0.4 and e < 0:
                st.markdown('<div class="diag-box diag-alert">🚨 **Stress Hydrique.** Manque d\'eau détecté. Déclenchez l\'irrigation.</div>', unsafe_allow_html=True)
            elif v < 0.4 and e >= 0:
                st.markdown('<div class="diag-box diag-warn">⚠️ **Anomalie.** Eau suffisante mais croissance faible. Visite terrain recommandée (insectes/maladie).</div>', unsafe_allow_html=True)
            else:
                st.markdown('<div class="diag-box diag-ok">✅ **Culture Saine.** Votre parcelle est en bon état général.</div>', unsafe_allow_html=True)

            # Affichage de la carte
            col_m, col_o = st.columns([5, 1])
            with col_o:
                mode_carte = st.radio("🗺️ Couche :", ["Vigueur (NDVI)", "Eau (NDWI)"])
                st.markdown("---")
                if "Vigueur" in mode_carte:
                    st.write("🟢 Excellent\n\n🟡 Moyen\n\n🔴 Faible")
                else:
                    st.write("🔵 Hydraté\n\n⚪ Moyen\n\n🟤 Sec")
            
            with col_m:
                m = folium.Map(location=[(lat_up+lat_down)/2, (lon_left+lon_right)/2], zoom_start=16)
                folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='Satellite').add_to(m)
                
                tile = res['vigueur_url'] if "Vigueur" in mode_carte else res['eau_url']
                folium.TileLayer(tiles=tile, attr='GEE', overlay=True, opacity=0.8).add_to(m)
                
                # Rectangle jaune pour délimiter la parcelle
                folium.Rectangle(bounds=[[lat_down, lon_left], [lat_up, lon_right]], color='#FFD600', weight=2.5, fill=False).add_to(m)
                
                # CORRECTION ICI : Ajout de la "key" pour forcer la mise à jour visuelle des couches
                st_folium(m, width=900, height=450, returned_objects=[], key=f"carte_{mode_carte}")

        # --- PARTIE 2 : LE GRAPHIQUE HISTORIQUE ---
        st.markdown("---")
        st.markdown(f"### 📈 Évolution temporelle (du {date_debut.strftime('%d/%m/%Y')} au {date_analyse.strftime('%d/%m/%Y')})")
        
        raw_data = get_time_series(roi_coords, str(date_debut), str(date_analyse))
        if raw_data:
            df = pd.DataFrame([f['properties'] for f in raw_data]).dropna()
            df['Date'] = pd.to_datetime(df['t'], unit='ms')
            
            fig = px.line(df, x='Date', y=['v', 'e'], labels={'value': 'Indice', 'variable': 'Indicateur'},
                          color_discrete_map={'v': '#2e7d32', 'e': '#0277bd'})
            
            # Personnaliser le nom des courbes dans la légende
            newnames = {'v': 'Vigueur (NDVI)', 'e': 'État Hydrique (NDWI)'}
            fig.for_each_trace(lambda t: t.update(name = newnames[t.name], legendgroup = newnames[t.name]))
            
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Aucune donnée historique trouvée pour cette période (images trop nuageuses).")

else:
    st.info("👈 Configurez votre parcelle et vos dates dans le menu de gauche, puis cliquez sur 'Lancer le traitement'.")