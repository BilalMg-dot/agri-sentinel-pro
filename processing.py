import ee
import json
import streamlit as st
from datetime import date
 
 
# ─────────────────────────────────────────────
# INITIALISATION GEE
# ─────────────────────────────────────────────
@st.cache_resource
def init_gee():
    """Initialise la connexion à Google Earth Engine via les Secrets Streamlit."""
    try:
        key_dict = dict(st.secrets['earth_engine'])
        creds = ee.ServiceAccountCredentials(
            key_dict['client_email'],
            key_data=json.dumps(key_dict)
        )
        ee.Initialize(creds, project=key_dict['project_id'])
        return True
    except Exception as e:
        st.error(f"Erreur de connexion à GEE : {e}")
        return False
 
 
# ─────────────────────────────────────────────
# HELPERS INTERNES
# ─────────────────────────────────────────────
def _build_collection(roi, date_start, date_end, cloud_threshold=15):
    """Construit une collection Sentinel-2 filtrée, triée par clarté."""
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(date_start, date_end)
        .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_threshold))
        .sort('system:time_start', False)
    )
 
 
def _compute_indices(image, roi):
    """Calcule NDVI (vigueur) et NDWI (eau) et les découpe sur le ROI."""
    vigueur = image.normalizedDifference(['B8', 'B4']).rename('vigueur').clip(roi)
    eau     = image.normalizedDifference(['B8', 'B11']).rename('eau').clip(roi)
    return vigueur, eau
 
 
def _viz_params():
    """Paramètres de visualisation standard pour les deux indices."""
    return (
        {'min': 0.1, 'max': 0.8, 'palette': ['#ff4b4b', '#f1c40f', '#2ecc71']},   # vigueur
        {'min': -0.2, 'max': 0.5, 'palette': ['#8d6e63', '#ffffff', '#3498db']},   # eau
    )
 
 
def _get_mean_stats(vigueur, eau, roi, scale=10):
    """Réduit les deux bandes à leur moyenne sur le ROI."""
    return (
        vigueur.addBands(eau)
        .reduceRegion(reducer=ee.Reducer.mean(), geometry=roi, scale=scale)
        .getInfo()
    )
 
 
def _tile_url(image, viz):
    return image.getMapId(viz)['tile_fetcher'].url_format
 
 
# ─────────────────────────────────────────────
# ANALYSE COURANTE
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_analysis_data(roi_coords, target_date_str, cloud_threshold=15):
    """
    Récupère la meilleure image Sentinel-2 des 45 derniers jours
    et retourne vigueur, état hydrique, URLs de tuiles.
 
    Paramètres
    ----------
    roi_coords      : [lon_left, lat_down, lon_right, lat_up]
    target_date_str : 'YYYY-MM-DD'
    cloud_threshold : % max de nuages (défaut 15)
 
    Retourne
    --------
    dict avec clés : date_capture, vigueur_avg, eau_avg,
                     vigueur_url, eau_url
    ou {'error': message}
    """
    try:
        roi         = ee.Geometry.Rectangle(roi_coords)
        target_date = ee.Date(target_date_str)
 
        col = _build_collection(
            roi,
            target_date.advance(-45, 'day'),
            target_date.advance(1, 'day'),
            cloud_threshold
        )
 
        # ── Timeout implicite : si la collection est vide → message clair ──
        size = col.size().getInfo()
        if size == 0:
            # Essai avec seuil plus permissif
            col2 = _build_collection(
                roi,
                target_date.advance(-60, 'day'),
                target_date.advance(1, 'day'),
                cloud_threshold=30
            )
            size2 = col2.size().getInfo()
            if size2 == 0:
                return {
                    "error": (
                        "☁️ Aucune image exploitable trouvée (trop nuageux). "
                        "Essayez une autre date ou attendez une météo plus clémente."
                    )
                }
            col = col2
 
        image       = col.first()
        actual_date = image.date().format('dd/MM/YYYY').getInfo()
 
        vigueur, eau = _compute_indices(image, roi)
        stats        = _get_mean_stats(vigueur, eau, roi)
        viz_v, viz_e = _viz_params()
 
        return {
            "date_capture": actual_date,
            "vigueur_avg":  stats.get('vigueur'),
            "eau_avg":      stats.get('eau'),
            "vigueur_url":  _tile_url(vigueur, viz_v),
            "eau_url":      _tile_url(eau,     viz_e),
            # Stocker l'image brute pour detect_problem_zones
            "_image_id":    image.id().getInfo(),
        }
 
    except ee.EEException as gee_err:
        msg = str(gee_err)
        if "Computation timed out" in msg or "deadline" in msg.lower():
            return {"error": "⏱️ Délai GEE dépassé. Essayez une zone plus petite ou réessayez dans quelques instants."}
        if "memory" in msg.lower():
            return {"error": "💾 Mémoire GEE insuffisante pour cette zone. Réduisez la surface de la parcelle."}
        return {"error": f"Erreur GEE : {msg}"}
    except Exception as e:
        return {"error": str(e)}
 
 
# ─────────────────────────────────────────────
# COMPARAISON DEUX DATES
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_comparison_data(roi_coords, date_ref_str, date_now_str, cloud_threshold=20):
    """
    Compare la vigueur entre deux dates.
 
    Retourne un dict avec :
      vigueur_ref, vigueur_now, eau_ref, eau_now,
      vigueur_url_ref, vigueur_url_now, date_ref, date_now
    ou {'error': message}
    """
    try:
        roi = ee.Geometry.Rectangle(roi_coords)
        viz_v, viz_e = _viz_params()
        results = {}
 
        for label, d_str in [('ref', date_ref_str), ('now', date_now_str)]:
            d    = ee.Date(d_str)
            col  = _build_collection(roi, d.advance(-30, 'day'), d.advance(1, 'day'), cloud_threshold)
            size = col.size().getInfo()
            if size == 0:
                return {"error": f"Aucune image pour la date {d_str} (± 30 jours)."}
            img          = col.first()
            vig, eau     = _compute_indices(img, roi)
            stats        = _get_mean_stats(vig, eau, roi)
            results[f'vigueur_{label}']     = stats.get('vigueur') or 0.0
            results[f'eau_{label}']         = stats.get('eau')     or 0.0
            results[f'vigueur_url_{label}'] = _tile_url(vig, viz_v)
            results[f'date_{label}']        = img.date().format('dd/MM/YYYY').getInfo()
 
        return results
 
    except Exception as e:
        return {"error": str(e)}
 
 
# ─────────────────────────────────────────────
# SÉRIE TEMPORELLE
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def get_time_series(roi_coords, start_date, end_date, cloud_threshold=20):
    """
    Récupère la série temporelle NDVI + NDWI sur la période.
 
    Retourne une liste de features GEE [{properties: {t, v, e}}, ...]
    """
    try:
        roi = ee.Geometry.Rectangle(roi_coords)
        col = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(roi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_threshold))
        )
 
        def extract(img):
            m = (
                img.normalizedDifference(['B8', 'B4']).rename('v')
                .addBands(img.normalizedDifference(['B8', 'B11']).rename('e'))
                .reduceRegion(ee.Reducer.mean(), roi, 10)
            )
            return ee.Feature(None, {
                't': img.date().millis(),
                'v': m.get('v'),
                'e': m.get('e'),
            })
 
        features = col.map(extract).getInfo()['features']
        return features
 
    except Exception as e:
        st.warning(f"Erreur série temporelle : {e}")
        return []
 
 
# ─────────────────────────────────────────────
# DÉTECTION ZONES PROBLÉMATIQUES
# ─────────────────────────────────────────────
@st.cache_data(ttl=3600, show_spinner=False)
def detect_problem_zones(roi_coords, analysis_result, ndvi_threshold=0.35, grid_size=4):
    """
    Divise le ROI en une grille (grid_size × grid_size) et identifie
    les cellules dont le NDVI moyen est inférieur au seuil.
 
    Retourne une liste de dicts :
      [{'bounds': [[lat_s, lon_w], [lat_n, lon_e]], 'ndvi': float}, ...]
    """
    # Si pas d'analyse ou erreur → liste vide (pas de blocage de l'UI)
    if not analysis_result or "error" in analysis_result:
        return []
 
    try:
        lon_l, lat_s, lon_r, lat_n = roi_coords
        lon_step = (lon_r - lon_l) / grid_size
        lat_step = (lat_n - lat_s) / grid_size
 
        problem_zones = []
 
        for i in range(grid_size):
            for j in range(grid_size):
                cell_lon_l = lon_l + i * lon_step
                cell_lon_r = cell_lon_l + lon_step
                cell_lat_s = lat_s + j * lat_step
                cell_lat_n = cell_lat_s + lat_step
 
                cell_roi  = ee.Geometry.Rectangle([cell_lon_l, cell_lat_s, cell_lon_r, cell_lat_n])
                # Réutiliser la date de l'analyse courante
                target_d  = ee.Date(date.today().isoformat())
                col       = _build_collection(cell_roi, target_d.advance(-45, 'day'),
                                              target_d.advance(1, 'day'), cloud_threshold=20)
                if col.size().getInfo() == 0:
                    continue
                img   = col.first()
                ndvi  = img.normalizedDifference(['B8', 'B4'])
                stats = ndvi.reduceRegion(
                    reducer=ee.Reducer.mean(), geometry=cell_roi, scale=20
                ).getInfo()
                val = stats.get('nd')
                if val is not None and val < ndvi_threshold:
                    problem_zones.append({
                        'bounds': [[cell_lat_s, cell_lon_l], [cell_lat_n, cell_lon_r]],
                        'ndvi':   val,
                    })
 
        return problem_zones
 
    except Exception:
        # Dégradation gracieuse : on ne bloque pas l'affichage de la carte
        return []
 
 
# ─────────────────────────────────────────────
# EXPORT GEOTIFF (URL de téléchargement GEE)
# ─────────────────────────────────────────────
def export_geotiff_url(roi_coords, analysis_result, mode="Vigueur"):
    """
    Génère une URL de téléchargement GEE pour le GeoTIFF de la bande choisie.
    Utilise ee.data.makeDownloadUrl (taille limitée ~10 Mo, parfait pour parcelles).
    """
    if not analysis_result or "error" in analysis_result:
        return None
    try:
        roi         = ee.Geometry.Rectangle(roi_coords)
        target_d    = ee.Date(date.today().isoformat())
        col         = _build_collection(roi, target_d.advance(-45, 'day'),
                                        target_d.advance(1, 'day'), cloud_threshold=20)
        if col.size().getInfo() == 0:
            return None
 
        img    = col.first()
        band   = img.normalizedDifference(['B8', 'B4']).rename('NDVI')  if mode == "Vigueur" \
            else img.normalizedDifference(['B8', 'B11']).rename('NDWI')
 
        url = band.getDownloadURL({
            'region':  roi,
            'scale':   10,
            'format':  'GeoTIFF',
            'name':    f'agri_sentinel_{mode.lower()}',
        })
        return url
    except Exception:
        return None