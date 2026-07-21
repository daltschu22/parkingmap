// Parking Map - Main JavaScript

// Initialize map centered across Somerville, Medford, and Cambridge, MA
const map = L.map('map', { preferCanvas: true }).setView([42.3925, -71.1090], 13);
// Canvas renderer tolerance expands hit area without changing visible stroke width.
const hitRenderer = L.canvas({ tolerance: 8 });

// Base map layers
const darkBaseLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19
});

const satelliteBaseLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19
});

darkBaseLayer.addTo(map);

const layerControl = L.control.layers({
    'Dark': darkBaseLayer,
    'Satellite': satelliteBaseLayer
}, null, {
    position: 'topright',
    collapsed: true
}).addTo(map);

// Layer groups for streets
let allStreetsLayer = null;
let searchResultsLayer = null;
let meterEvidenceLayer = null;
let accessibleEvidenceLayer = null;
let referenceSignLayer = null;
let referenceMatchLayer = null;
let evidenceKeyControl = null;
const evidenceLoadPromises = new Map();
const loadedEvidenceLayers = new Set();
let hoveredStreetName = null;
let selectedStreetName = null;
let hoverResetTimer = null;

function setBaseLayer(layerName) {
    const useSatellite = layerName === 'satellite';
    const nextLayer = useSatellite ? satelliteBaseLayer : darkBaseLayer;
    const previousLayer = useSatellite ? darkBaseLayer : satelliteBaseLayer;

    if (map.hasLayer(previousLayer)) {
        map.removeLayer(previousLayer);
    }
    if (!map.hasLayer(nextLayer)) {
        nextLayer.addTo(map);
        nextLayer.bringToBack();
    }

    document.querySelectorAll('.map-mode-btn').forEach((button) => {
        const isActive = button.dataset.baseLayer === layerName;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-pressed', String(isActive));
    });
}

// Four driver-facing street states. Evidence markers use separate colors below.
const COLORS = {
    metered: '#0ea5e9',
    openTimeLimited: '#22c55e',
    restricted: '#ef4444',
    unknown: '#94a3b8',
    inactiveMeterEvidence: '#64748b',
    accessibleEvidence: '#facc15',
    highlight: '#f59e0b',
    hover: '#3b82f6'
};

function escapeHtml(value) {
    return String(value ?? '')
        .replaceAll('&', '&amp;')
        .replaceAll('<', '&lt;')
        .replaceAll('>', '&gt;')
        .replaceAll('"', '&quot;')
        .replaceAll("'", '&#039;');
}

function safeExternalUrl(value) {
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return url.protocol === 'https:' ? url.href : '';
    } catch (_error) {
        return '';
    }
}

function setAppStatus(message = '', isError = false) {
    const status = document.getElementById('app-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('error', isError);
}

async function fetchJson(url) {
    const response = await fetch(url, { headers: { Accept: 'application/json' } });
    if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`);
    }
    return response.json();
}

function getFeatureDisplayStatus(properties = {}) {
    if (properties.PARKING_DISPLAY_STATUS) {
        return properties.PARKING_DISPLAY_STATUS;
    }

    // Backward-compatible fallback for responses cached before display status existed.
    const access = properties.PARKING_ACCESS || 'unknown';
    if (access === 'permit_with_metered_segments') return 'metered';
    if (access === 'permit_with_time_limited_segments') return 'open_time_limited';
    if (['resident_permit_required', 'resident_permit_segment_rules_known', 'private_rules_apply'].includes(access)) {
        return 'restricted';
    }
    return 'unknown';
}

// Style streets with the same four meanings in every municipality.
function getStreetStyle(feature) {
    const properties = feature.properties || {};
    const displayStatus = getFeatureDisplayStatus(properties);
    const access = properties.PARKING_ACCESS || 'unknown';
    const municipality = String(properties.MUNICIPALITY || '').trim().toLowerCase();
    const detailed = map.getZoom() >= 15;
    const statusWeight = detailed ? 2.5 : 1.5;

    if (displayStatus === 'metered') {
        return { color: COLORS.metered, weight: statusWeight, opacity: 0.9, dashArray: null };
    }
    if (displayStatus === 'open_time_limited') {
        return { color: COLORS.openTimeLimited, weight: statusWeight, opacity: 0.9, dashArray: null };
    }
    if (displayStatus === 'restricted') {
        const mixedOrPrivate = ['resident_permit_segment_rules_known', 'private_rules_apply'].includes(access);
        return {
            color: COLORS.restricted,
            weight: statusWeight,
            opacity: 0.9,
            dashArray: mixedOrPrivate ? '6 4' : null
        };
    }

    return {
        color: COLORS.unknown,
        weight: detailed ? (municipality === 'cambridge' ? 1.75 : 2) : 1,
        opacity: municipality === 'cambridge' ? 0.62 : 0.7,
        dashArray: municipality === 'cambridge' ? null : '2 5'
    };
}

// Style for highlighted/searched streets
const highlightStyle = {
    color: COLORS.highlight,
    weight: 4,
    opacity: 1,
    dashArray: null
};

// Style on hover
const hoverStyle = {
    color: COLORS.hover,
    weight: 5,
    opacity: 1,
    dashArray: null
};

const selectedStyle = {
    color: '#fde047',
    weight: 6,
    opacity: 1,
    dashArray: null
};

function getStreetKey(feature) {
    const props = feature?.properties || {};
    const municipality = String(props.MUNICIPALITY || '').trim().toUpperCase();
    const street = String(props.STNAME || '').trim().toUpperCase();
    if (!street) return '';
    return `${municipality}:${street}`;
}

function getLayerBaseStyle(layer) {
    if (layer._isSearchResult) {
        return { ...highlightStyle };
    }
    return getStreetStyle(layer.feature);
}

function isLayerSelected(layer) {
    if (!selectedStreetName) return false;
    return getStreetKey(layer.feature) === selectedStreetName;
}

function forEachStreetLayer(callback) {
    for (const group of [allStreetsLayer, searchResultsLayer]) {
        if (!group) continue;
        group.eachLayer((layer) => {
            if (layer?.feature) {
                callback(layer);
            }
        });
    }
}

function applyLayerRestStyle(layer) {
    if (isLayerSelected(layer)) {
        layer.setStyle(selectedStyle);
    } else {
        layer.setStyle(getLayerBaseStyle(layer));
    }
}

function refreshSelectionStyles() {
    forEachStreetLayer((layer) => {
        applyLayerRestStyle(layer);
    });
}

function selectStreet(streetName) {
    selectedStreetName = String(streetName || '').trim().toUpperCase() || null;
    resetHover();
    refreshSelectionStyles();
}

function resetHover() {
    if (hoverResetTimer) {
        clearTimeout(hoverResetTimer);
        hoverResetTimer = null;
    }
    if (!hoveredStreetName) return;
    const streetKey = hoveredStreetName;
    hoveredStreetName = null;
    forEachStreetLayer((layer) => {
        if (getStreetKey(layer.feature) === streetKey) {
            applyLayerRestStyle(layer);
        }
    });
}

function applyHoverStreet(streetName) {
    if (hoverResetTimer) {
        clearTimeout(hoverResetTimer);
        hoverResetTimer = null;
    }

    const streetKey = String(streetName || '').trim().toUpperCase();
    if (!streetKey) return;
    if (hoveredStreetName === streetKey) return;

    if (hoveredStreetName && hoveredStreetName !== streetKey) {
        resetHover();
    }

    hoveredStreetName = streetKey;
    forEachStreetLayer((layer) => {
        if (getStreetKey(layer.feature) === streetKey) {
            layer.setStyle(hoverStyle);
        }
    });
}

function scheduleHoverReset(streetName) {
    const streetKey = String(streetName || '').trim().toUpperCase();
    if (!streetKey || hoveredStreetName !== streetKey) return;
    if (hoverResetTimer) {
        clearTimeout(hoverResetTimer);
    }
    // Avoid flicker / lost clicks while moving across adjacent segments.
    hoverResetTimer = setTimeout(() => {
        hoverResetTimer = null;
        if (hoveredStreetName === streetKey) {
            resetHover();
        }
    }, 40);
}

// Format property labels for display
function formatLabel(key) {
    const labels = {
        'MUNICIPALITY': 'Municipality',
        'STNAME': 'Street Name',
        'ONEWAY': 'One Way',
        'OWNERSHIP': 'Ownership',
        'FUNC_CLASS': 'Road Class',
        'MATERIAL': 'Material',
        'ROW_WIDTH': 'ROW Width',
        'PAVE_WIDTH': 'Pave Width',
        'PARKING_ACCESS': 'Parking Access',
        'PARKING_NOTE': 'Parking Note',
        'PARKING_RULE_SOURCE': 'Rule Source',
        'PARKING_CONFIDENCE': 'Confidence',
        'PARKING_DATA_UPDATED_AT': 'Data Generated',
        'PARKING_RULE_MATCH_LEVEL': 'Rule Match',
        'PARKING_EVIDENCE': 'Mapped Evidence',
        'PARKING_EVIDENCE_SOURCE': 'Evidence Source',
        'PARKING_EVIDENCE_MATCH_LEVEL': 'Evidence Match',
        'PARKING_EVIDENCE_CONFIDENCE': 'Evidence Confidence',
        'PARKING_MEDFORD_RULE_COUNT': 'Medford Rule Rows',
        'PARKING_MEDFORD_PARTIAL_RULE_COUNT': 'Partial Rule Rows',
        'PARKING_MEDFORD_RULE_SUMMARY': 'Medford Rule Summary',
        'PARKING_CAMBRIDGE_METER_COUNT_ESTIMATE': 'Cambridge Meter Spaces',
        'PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE': 'Active Meter Spaces',
        'PARKING_CAMBRIDGE_INACTIVE_METER_COUNT_ESTIMATE': 'Inactive/Other Meter Spaces',
        'PARKING_CAMBRIDGE_ACCESSIBLE_SPACE_COUNT': 'Accessible Spaces',
        'PARKING_CAMBRIDGE_METER_HOURS': 'Meter Hours',
        'PARKING_CAMBRIDGE_METER_MAX_TIMES': 'Meter Max Times',
        'PARKING_CAMBRIDGE_METER_RATES': 'Meter Rates',
        'PARKING_CAMBRIDGE_MATCH_DISTANCE': 'Nearest-Centerline Distance',
        'FROM_STREET': 'From Street',
        'TO_STREET': 'To Street',
        'ROAD_TYPE': 'Road Type'
    };
    return labels[key] || key;
}

function getDisplayStatusColor(status) {
    if (status === 'metered') return COLORS.metered;
    if (status === 'open_time_limited') return COLORS.openTimeLimited;
    if (status === 'restricted') return COLORS.restricted;
    return COLORS.unknown;
}

function getAtGlanceAnswer(properties = {}) {
    const municipality = String(properties.MUNICIPALITY || '').trim().toLowerCase();
    const access = properties.PARKING_ACCESS || 'unknown';
    const evidence = properties.PARKING_EVIDENCE || 'none';
    const displayStatus = getFeatureDisplayStatus(properties);

    if (municipality === 'cambridge' && evidence === 'active_meter_spaces_nearby') {
        return {
            tone: 'metered',
            title: 'Metered spaces mapped nearby',
            summary: 'Use the exact blue meter markers. Other curb sections remain unknown, so check posted signs.'
        };
    }
    if (municipality === 'cambridge' && evidence === 'accessible_spaces_nearby') {
        return {
            tone: 'unknown',
            title: 'Accessible spaces mapped nearby',
            summary: 'Use the exact yellow markers. General parking on the rest of this curb is still unknown.'
        };
    }
    if (municipality === 'cambridge' && evidence === 'inactive_meter_spaces_nearby') {
        return {
            tone: 'unknown',
            title: 'No active meter confirmed',
            summary: 'Nearby meter evidence is inactive, removed, or proposed. Check posted signs before parking.'
        };
    }
    if (displayStatus === 'metered') {
        return {
            tone: 'metered',
            title: 'Metered sections exist',
            summary: 'Park only at a marked meter. Other sections on this street may require a resident permit.'
        };
    }
    if (displayStatus === 'open_time_limited') {
        return {
            tone: 'open',
            title: 'Open / time-limited sections exist',
            summary: 'Check the exact curb signs and hours. Other sections may require a resident permit.'
        };
    }
    if (displayStatus === 'restricted' && access === 'private_rules_apply') {
        return {
            tone: 'restricted',
            title: 'Private street — do not assume public parking',
            summary: 'Parking is controlled by the property owner. Look for posted authorization and restrictions.'
        };
    }
    if (displayStatus === 'restricted' && access === 'resident_permit_segment_rules_known') {
        return {
            tone: 'restricted',
            title: 'Permit rules vary by block',
            summary: 'A permit restriction is documented for part of this street. Confirm the exact block and curb signs.'
        };
    }
    if (displayStatus === 'restricted') {
        return {
            tone: 'restricted',
            title: 'Resident permit required',
            summary: 'Without the required permit, do not assume parking is allowed. Posted exceptions still control.'
        };
    }
    return {
        tone: 'unknown',
        title: 'Unknown — check posted signs',
        summary: 'The current data cannot answer for this exact curb. Do not infer permission from the map alone.'
    };
}

function getOwnershipText(rawOwnership) {
    const ownership = String(rawOwnership || '').trim();
    if (!ownership) {
        return 'Unknown';
    }
    return ownership;
}

function getParkingEvidenceText(value) {
    const labels = {
        active_meter_spaces_nearby: 'Active meter spaces nearby',
        inactive_meter_spaces_nearby: 'Inactive/removed/proposed meter spaces nearby',
        accessible_spaces_nearby: 'Accessible spaces nearby',
        none: 'No mapped point evidence nearby'
    };
    return labels[String(value || '').trim()] || value || 'None';
}

// Format one-way value
function formatOneway(value) {
    const normalized = String(value ?? '').trim().toUpperCase();
    if (normalized === 'F' || normalized === '0') return 'No';
    if (['T', 'TF', 'FT', '1', '-1'].includes(normalized)) return 'Yes';
    return value || 'Unknown';
}

// Show street details in sidebar
function showStreetDetails(properties) {
    const container = document.getElementById('street-details');
    const hint = document.querySelector('.info-hint');
    
    if (hint) hint.style.display = 'none';
    
    const answer = getAtGlanceAnswer(properties);
    const statusColor = getDisplayStatusColor(answer.tone === 'open' ? 'open_time_limited' : answer.tone);
    const parkingNote = properties.PARKING_NOTE || 'No additional parking rule note available.';
    const meterCount = properties.PARKING_METER_COUNT_ESTIMATE;
    const meterConfidence = properties.PARKING_METER_COUNT_CONFIDENCE || 'none';
    
    let html = `
        <div class="parking-answer parking-answer--${escapeHtml(answer.tone)}" style="--answer-color: ${statusColor};">
            <span class="parking-answer-location">${escapeHtml(properties.MUNICIPALITY || 'Unknown')} · ${escapeHtml(properties.STNAME || 'Unknown street')}</span>
            <strong class="parking-answer-title">${escapeHtml(answer.title)}</strong>
            <span class="parking-answer-summary">${escapeHtml(answer.summary)}</span>
        </div>
    `;
    let detailHtml = `
        <div class="detail-row">
            <span class="detail-label">Source detail</span>
            <span class="detail-value">${escapeHtml(parkingNote)}</span>
        </div>
    `;

    if (meterCount !== null && meterCount !== undefined) {
        detailHtml += `
            <div class="detail-row">
                <span class="detail-label">Meter Count Estimate</span>
                <span class="detail-value">${escapeHtml(meterCount)} (${escapeHtml(meterConfidence)})</span>
            </div>
        `;
    }
    
    const displayProps = [
        'MUNICIPALITY',
        'STNAME',
        'OWNERSHIP',
        'OWNERSHIP_SOURCE',
        'OWNERSHIP_CONFIDENCE',
        'FUNC_CLASS',
        'ROAD_TYPE',
        'FROM_STREET',
        'TO_STREET',
        'ONEWAY',
        'MATERIAL',
        'PARKING_RULE_SOURCE',
        'PARKING_CONFIDENCE',
        'PARKING_DATA_UPDATED_AT',
        'PARKING_RULE_MATCH_LEVEL',
        'PARKING_EVIDENCE',
        'PARKING_EVIDENCE_SOURCE',
        'PARKING_EVIDENCE_MATCH_LEVEL',
        'PARKING_EVIDENCE_CONFIDENCE',
        'PARKING_MEDFORD_RULE_COUNT',
        'PARKING_MEDFORD_PARTIAL_RULE_COUNT',
        'PARKING_MEDFORD_RULE_SUMMARY',
        'PARKING_CAMBRIDGE_METER_COUNT_ESTIMATE',
        'PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE',
        'PARKING_CAMBRIDGE_INACTIVE_METER_COUNT_ESTIMATE',
        'PARKING_CAMBRIDGE_ACCESSIBLE_SPACE_COUNT',
        'PARKING_CAMBRIDGE_METER_HOURS',
        'PARKING_CAMBRIDGE_METER_MAX_TIMES',
        'PARKING_CAMBRIDGE_METER_RATES',
        'PARKING_CAMBRIDGE_MATCH_DISTANCE'
    ];
    
    for (const key of displayProps) {
        let value = properties[key];
        if (key === 'ONEWAY') {
            value = formatOneway(value);
        } else if (key === 'OWNERSHIP') {
            value = getOwnershipText(value);
        } else if (key === 'PARKING_EVIDENCE') {
            value = getParkingEvidenceText(value);
        }
        if (value !== null && value !== undefined && value !== '') {
            const sourceUrl = key === 'PARKING_RULE_SOURCE'
                ? safeExternalUrl(properties.PARKING_SOURCE_URL)
                : key === 'PARKING_EVIDENCE_SOURCE'
                    ? safeExternalUrl(properties.PARKING_EVIDENCE_SOURCE_URL)
                    : '';
            const renderedValue = sourceUrl
                ? `<a href="${escapeHtml(sourceUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
                : escapeHtml(value);
            detailHtml += `
                <div class="detail-row">
                    <span class="detail-label">${escapeHtml(formatLabel(key))}</span>
                    <span class="detail-value">${renderedValue}</span>
                </div>
            `;
        }
    }

    html += `
        <details class="technical-details">
            <summary>Source and rule details</summary>
            <div class="technical-details-body">${detailHtml}</div>
        </details>
    `;
    container.innerHTML = html || '<p class="info-hint">No details available</p>';
}

// Create popup content
function createPopup(properties) {
    const name = properties.STNAME || 'Unknown Street';
    const municipality = properties.MUNICIPALITY || 'Unknown';
    const answer = getAtGlanceAnswer(properties);
    return `<strong>${escapeHtml(name)}</strong><br>${escapeHtml(municipality)}<br><strong>${escapeHtml(answer.title)}</strong><br>${escapeHtml(answer.summary)}`;
}

function createMeterEvidencePopup(properties) {
    const status = properties.STATUS || 'Unknown status';
    const hours = properties.OPERATION_HOURS || 'Hours unknown';
    const maxTime = properties.MAX_TIME || 'Max time unknown';
    const rate = properties.RATE || 'Rate unknown';
    const matchedStreet = properties.MATCHED_STREET || properties.NEAREST_STREET;
    const matchDistance = Number(properties.MATCH_DISTANCE_METERS);
    const matchText = matchedStreet && Number.isFinite(matchDistance)
        ? `<br>Nearest centerline: ${escapeHtml(matchedStreet)} (${escapeHtml(matchDistance.toFixed(1))} m, approximate)`
        : '<br>Nearest-street match unavailable';
    return `<strong>Cambridge meter space</strong><br>${escapeHtml(status)}<br>${escapeHtml(hours)}<br>${escapeHtml(maxTime)}<br>${escapeHtml(rate)}${matchText}`;
}

function createAccessibleEvidencePopup(properties) {
    const street = properties.STNAME || 'Unknown street';
    const side = properties.SIDE_OF_STREET || 'Side unknown';
    return `<strong>Cambridge accessible space</strong><br>${escapeHtml(street)}<br>${escapeHtml(side)}`;
}

function compactObjectValue(value) {
    return String(value || 'Unknown')
        .replace(/^regulatory--/, '')
        .replace(/^information--/, '')
        .replace(/^object--/, '')
        .replace(/--g\d+$/, '')
        .replaceAll('--', ' / ')
        .replaceAll('-', ' ');
}

function createReferenceSignPopup(properties) {
    const objectValue = compactObjectValue(properties.OBJECT_VALUE);
    const firstSeen = properties.FIRST_SEEN_AT || 'First seen unknown';
    const imageCount = properties.LINKED_IMAGE_COUNT || 0;
    const source = safeExternalUrl(properties.SOURCE_URL);
    const sourceLink = source
        ? `<br><a href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">Mapillary feature</a>`
        : '';
    return `<strong>Mapillary parking sign</strong><br>${escapeHtml(objectValue)}<br>${escapeHtml(firstSeen)}<br>${escapeHtml(imageCount)} linked image(s)${sourceLink}`;
}

function createReferenceMatchPopup(properties) {
    const detectedObject = properties.DETECTED_OBJECT || 'Detected object';
    const confidence = Number(properties.OBJECT_CONFIDENCE || 0).toFixed(3);
    const referenceClass = compactObjectValue(properties.REFERENCE_OBJECT_VALUE);
    const imageId = properties.IMAGE_ID || 'Unknown image';
    const source = safeExternalUrl(properties.REFERENCE_SOURCE_URL);
    const sourceLink = source
        ? `<br><a href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">Mapillary feature</a>`
        : '';
    return `<strong>Detection matched to reference</strong><br>${escapeHtml(detectedObject)} (${escapeHtml(confidence)})<br>${escapeHtml(referenceClass)}<br>Image ${escapeHtml(imageId)}${sourceLink}`;
}

function focusStreetFromSearch(query, data) {
    if (!data?.features?.length || !searchResultsLayer) return;

    const q = String(query || '').trim().toUpperCase();
    const byName = new Map();
    for (const feature of data.features) {
        const name = String(feature?.properties?.STNAME || '').trim().toUpperCase();
        if (!name) continue;
        const municipality = String(feature?.properties?.MUNICIPALITY || '').trim().toUpperCase();
        const key = `${municipality}:${name}`;
        if (!byName.has(key)) {
            byName.set(key, feature);
        }
    }
    if (byName.size === 0) return;

    let targetFeature = null;
    for (const [key, feature] of byName.entries()) {
        if (key.endsWith(`:${q}`) || key === q) {
            targetFeature = feature;
            break;
        }
    }
    if (!targetFeature) {
        targetFeature = byName.values().next().value;
    }

    const targetKey = getStreetKey(targetFeature);
    if (!targetKey) return;

    let popupLayer = null;
    searchResultsLayer.eachLayer((layer) => {
        if (popupLayer) return;
        if (getStreetKey(layer?.feature) === targetKey) {
            popupLayer = layer;
        }
    });
    if (popupLayer) {
        // Reuse the exact click code path so selection, popup and sidebar stay consistent.
        popupLayer.fire('click', { target: popupLayer });
    } else {
        // Fallback if a matching layer is unexpectedly missing.
        selectStreet(targetKey);
        showStreetDetails(targetFeature.properties);
    }
}

// Add interactivity to each feature
function onEachFeature(feature, layer) {
    layer._isSearchResult = false;

    layer.on({
        mouseover: function(e) {
            applyHoverStreet(getStreetKey(e.target.feature));
        },
        mouseout: function(e) {
            scheduleHoverReset(getStreetKey(e.target.feature));
        },
        click: function(e) {
            const props = feature.properties;
            selectStreet(getStreetKey(feature));
            showStreetDetails(props);
            e.target.bindPopup(createPopup(props)).openPopup();
            // Prevent hover color from sticking after click/popup interactions
            resetHover();
            applyLayerRestStyle(e.target);
        }
    });
}

// Special handler for search results - keeps highlight style
function onEachSearchFeature(feature, layer) {
    layer._isSearchResult = true;

    layer.on({
        mouseover: function(e) {
            applyHoverStreet(getStreetKey(e.target.feature));
        },
        mouseout: function(e) {
            scheduleHoverReset(getStreetKey(e.target.feature));
        },
        click: function(e) {
            const props = feature.properties;
            selectStreet(getStreetKey(feature));
            showStreetDetails(props);
            e.target.bindPopup(createPopup(props)).openPopup();
            // Keep highlighted search color after click
            resetHover();
            applyLayerRestStyle(e.target);
        }
    });
}

// Load and display all streets
async function loadStreets() {
    try {
        setAppStatus('Loading street data…');
        resetHover();
        const data = await fetchJson('/api/streets');
        
        if (allStreetsLayer) {
            map.removeLayer(allStreetsLayer);
        }
        
        allStreetsLayer = L.geoJSON(data, {
            style: getStreetStyle,
            renderer: hitRenderer,
            onEachFeature: onEachFeature
        }).addTo(map);
        refreshSelectionStyles();
        
        // Fit map to data bounds
        if (data.features && data.features.length > 0) {
            map.fitBounds(allStreetsLayer.getBounds(), { padding: [20, 20] });
        }
        
        console.log(`Loaded ${data.features?.length || 0} street segments`);
        setAppStatus('');
    } catch (error) {
        console.error('Error loading streets:', error);
        setAppStatus('Street data could not be loaded. Please retry.', true);
    }
}

function isActiveMeter(properties = {}) {
    return String(properties.STATUS || '').trim().toLowerCase() === 'in service';
}

function meterEvidenceStyle(properties = {}) {
    const color = isActiveMeter(properties) ? COLORS.metered : COLORS.inactiveMeterEvidence;
    const detailed = map.getZoom() >= 15;
    return {
        color,
        weight: detailed ? 1 : 0.5,
        opacity: detailed ? 0.9 : 0.35,
        fillColor: color,
        fillOpacity: detailed ? 0.45 : 0.12
    };
}

function evidenceLayerDefinitions() {
    return [
        {
            key: 'meters',
            label: 'Active Cambridge meter spaces',
            url: '/api/parking-evidence/cambridge/meters',
            group: meterEvidenceLayer,
            options: {
                renderer: hitRenderer,
                filter: (feature) => isActiveMeter(feature.properties || {}),
                style: (feature) => meterEvidenceStyle(feature.properties || {}),
                onEachFeature: (feature, layer) => {
                    layer.bindPopup(createMeterEvidencePopup(feature.properties || {}));
                }
            }
        },
        {
            key: 'accessible',
            label: 'Cambridge accessible spaces',
            url: '/api/parking-evidence/cambridge/accessible',
            group: accessibleEvidenceLayer,
            options: {
                pointToLayer: (_feature, latlng) => {
                    const detailed = map.getZoom() >= 15;
                    const marker = L.circleMarker(latlng, {
                        radius: detailed ? 4 : 2,
                        color: '#fde047',
                        weight: detailed ? 2 : 1,
                        fillColor: COLORS.accessibleEvidence,
                        fillOpacity: detailed ? 0.95 : 0.7
                    });
                    marker._parkingEvidenceKind = 'accessible';
                    return marker;
                },
                onEachFeature: (feature, layer) => {
                    layer.bindPopup(createAccessibleEvidencePopup(feature.properties || {}));
                }
            }
        },
        {
            key: 'reference-signs',
            label: 'Mapillary parking sign references',
            url: '/api/parking-evidence/imagery/reference-signs',
            group: referenceSignLayer,
            options: {
                pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
                    radius: 6,
                    color: '#fff7ed',
                    weight: 2,
                    fillColor: '#f97316',
                    fillOpacity: 0.85
                }),
                onEachFeature: (feature, layer) => {
                    layer.bindPopup(createReferenceSignPopup(feature.properties || {}));
                }
            }
        },
        {
            key: 'reference-matches',
            label: 'Reference-matched detections',
            url: '/api/parking-evidence/imagery/reference-matches',
            group: referenceMatchLayer,
            options: {
                pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
                    radius: 5,
                    color: '#ccfbf1',
                    weight: 2,
                    fillColor: '#14b8a6',
                    fillOpacity: 0.95
                }),
                onEachFeature: (feature, layer) => {
                    layer.bindPopup(createReferenceMatchPopup(feature.properties || {}));
                }
            }
        }
    ];
}

function syncEvidenceCheckbox(key, checked) {
    const input = document.querySelector(`[data-evidence-layer="${key}"]`);
    if (input) input.checked = checked;
    updateMapEvidenceKey();
}

const MAP_EVIDENCE_KEY_ITEMS = {
    meters: { color: COLORS.metered, label: 'Active meter space' },
    accessible: { color: COLORS.accessibleEvidence, label: 'Accessible space' },
    'reference-signs': { color: '#f97316', label: 'Mapillary sign' },
    'reference-matches': { color: '#14b8a6', label: 'Matched detection' }
};

function updateMapEvidenceKey() {
    const container = document.getElementById('map-evidence-key-items');
    const control = document.querySelector('.map-evidence-key');
    if (!container || !control) return;

    const visibleItems = Object.entries(MAP_EVIDENCE_KEY_ITEMS).filter(([key]) => {
        return document.querySelector(`[data-evidence-layer="${key}"]`)?.checked;
    });
    control.hidden = visibleItems.length === 0;
    container.innerHTML = visibleItems.map(([, item]) => `
        <span class="map-evidence-key-item">
            <span class="map-evidence-key-dot" style="background: ${item.color};"></span>
            ${escapeHtml(item.label)}
        </span>
    `).join('');
}

function setupMapEvidenceKey() {
    evidenceKeyControl = L.control({ position: 'bottomleft' });
    evidenceKeyControl.onAdd = () => {
        const container = L.DomUtil.create('div', 'map-evidence-key');
        container.setAttribute('role', 'note');
        container.setAttribute('aria-label', 'Visible map marker meanings');
        container.innerHTML = `
            <strong>Map dots</strong>
            <span id="map-evidence-key-items"></span>
        `;
        L.DomEvent.disableClickPropagation(container);
        return container;
    };
    evidenceKeyControl.addTo(map);
    updateMapEvidenceKey();
}

function addMeterCentroidMarkers(group, geoJsonLayer) {
    geoJsonLayer.eachLayer((layer) => {
        if (typeof layer.getBounds !== 'function') return;
        const bounds = layer.getBounds();
        if (!bounds.isValid()) return;
        const properties = layer.feature?.properties || {};
        const color = COLORS.metered;
        const marker = L.circleMarker(bounds.getCenter(), {
            renderer: hitRenderer,
            radius: map.getZoom() >= 15 ? 3 : 1.5,
            color,
            weight: map.getZoom() >= 15 ? 1 : 0.5,
            opacity: map.getZoom() >= 15 ? 0.95 : 0.55,
            fillColor: color,
            fillOpacity: map.getZoom() >= 15 ? 0.9 : 0.6
        }).bindPopup(createMeterEvidencePopup(properties)).addTo(group);
        marker._parkingEvidenceKind = 'active-meter-centroid';
    });
}

function updateEvidenceRendering() {
    const detailed = map.getZoom() >= 15;

    meterEvidenceLayer?.eachLayer((layer) => {
        if (layer._parkingEvidenceKind?.endsWith('meter-centroid')) {
            layer.setRadius(detailed ? 3 : 1.5);
            layer.setStyle({
                weight: detailed ? 1 : 0.5,
                opacity: detailed ? 0.95 : 0.55,
                fillOpacity: detailed ? 0.9 : 0.6
            });
            return;
        }
        if (typeof layer.eachLayer === 'function') {
            layer.eachLayer((shape) => {
                if (shape.feature && typeof shape.setStyle === 'function') {
                    shape.setStyle(meterEvidenceStyle(shape.feature.properties || {}));
                }
            });
        }
    });

    accessibleEvidenceLayer?.eachLayer((layer) => {
        if (typeof layer.eachLayer !== 'function') return;
        layer.eachLayer((marker) => {
            if (marker._parkingEvidenceKind !== 'accessible') return;
            marker.setRadius(detailed ? 4 : 2);
            marker.setStyle({ weight: detailed ? 2 : 1, fillOpacity: detailed ? 0.95 : 0.7 });
        });
    });
}

async function ensureEvidenceLayer(definition) {
    if (loadedEvidenceLayers.has(definition.key)) return;
    if (evidenceLoadPromises.has(definition.key)) {
        return evidenceLoadPromises.get(definition.key);
    }

    const loadPromise = fetchJson(definition.url)
        .then((data) => {
            definition.group.clearLayers();
            const geoJsonLayer = L.geoJSON(data, definition.options);
            definition.group.addLayer(geoJsonLayer);
            if (definition.key === 'meters') {
                addMeterCentroidMarkers(definition.group, geoJsonLayer);
            }
            loadedEvidenceLayers.add(definition.key);
            updateEvidenceRendering();
        })
        .catch((error) => {
            console.error(`Error loading ${definition.label}:`, error);
            if (map.hasLayer(definition.group)) map.removeLayer(definition.group);
            syncEvidenceCheckbox(definition.key, false);
            setAppStatus(`${definition.label} could not be loaded.`, true);
        })
        .finally(() => {
            evidenceLoadPromises.delete(definition.key);
        });
    evidenceLoadPromises.set(definition.key, loadPromise);
    return loadPromise;
}

function setupParkingEvidence() {
    meterEvidenceLayer = L.layerGroup();
    accessibleEvidenceLayer = L.layerGroup();
    referenceSignLayer = L.layerGroup();
    referenceMatchLayer = L.layerGroup();
    setupMapEvidenceKey();

    const definitions = evidenceLayerDefinitions();
    definitions.forEach((definition) => {
        layerControl.addOverlay(definition.group, definition.label);
    });

    map.on('overlayadd', (event) => {
        const definition = evidenceLayerDefinitions().find((item) => item.group === event.layer);
        if (!definition) return;
        syncEvidenceCheckbox(definition.key, true);
        void ensureEvidenceLayer(definition);
    });
    map.on('overlayremove', (event) => {
        const definition = evidenceLayerDefinitions().find((item) => item.group === event.layer);
        if (definition) syncEvidenceCheckbox(definition.key, false);
    });

    document.querySelectorAll('[data-evidence-layer]').forEach((input) => {
        input.addEventListener('change', () => {
            const definition = evidenceLayerDefinitions().find((item) => item.key === input.dataset.evidenceLayer);
            if (!definition) return;
            if (input.checked) {
                definition.group.addTo(map);
            } else {
                map.removeLayer(definition.group);
            }
            updateMapEvidenceKey();
        });
    });

    definitions.forEach((definition) => {
        const input = document.querySelector(`[data-evidence-layer="${definition.key}"]`);
        if (input?.checked && !map.hasLayer(definition.group)) {
            definition.group.addTo(map);
        }
    });
}

// Search streets by name
async function searchStreets(query) {
    try {
        setAppStatus(`Searching for ${query}…`);
        resetHover();
        const data = await fetchJson(`/api/streets/search?q=${encodeURIComponent(query)}`);
        
        // Remove previous search results layer
        if (searchResultsLayer) {
            map.removeLayer(searchResultsLayer);
            searchResultsLayer = null;
        }
        
        if (data.features && data.features.length > 0) {
            // Create search results layer with highlight style
            searchResultsLayer = L.geoJSON(data, {
                style: highlightStyle,
                renderer: hitRenderer,
                onEachFeature: onEachSearchFeature
            }).addTo(map);
            refreshSelectionStyles();
            
            // Fit map to search results
            map.fitBounds(searchResultsLayer.getBounds(), { padding: [50, 50] });
            focusStreetFromSearch(query, data);
            
            // Update stats to show search results
            const uniqueNames = new Set(data.features.map(f => `${f.properties?.MUNICIPALITY || ''}:${f.properties?.STNAME || ''}`).filter(Boolean));
            document.getElementById('stats-content').innerHTML = `
                <div class="stat-item">
                    <span class="stat-label">Search Results</span>
                    <span class="stat-value">${data.features.length} segments</span>
                </div>
                <div class="stat-item">
                    <span class="stat-label">Matching Streets</span>
                    <span class="stat-value">${uniqueNames.size}</span>
                </div>
            `;
            
            console.log(`Found ${data.features.length} matching segments`);
            setAppStatus(`Found ${uniqueNames.size} matching street${uniqueNames.size === 1 ? '' : 's'}.`);
        } else {
            console.log('No streets found matching query');
            setAppStatus(`No streets found matching "${query}".`, true);
        }
    } catch (error) {
        console.error('Error searching streets:', error);
        setAppStatus('Street search failed. Please retry.', true);
    }
}

// Load statistics
async function loadStats() {
    try {
        const stats = await fetchJson('/api/stats');
        
        const container = document.getElementById('stats-content');
        if (!container) return;
        const display = stats.parking_display || {};
        const evidence = stats.parking_evidence || {};

        container.innerHTML = `
            <div class="stat-item">
                <span class="stat-label">Total Segments</span>
                <span class="stat-value">${stats.total_segments.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label stat-label--metered">Metered sections</span>
                <span class="stat-value">${(display.metered || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label stat-label--open">Open / time-limited</span>
                <span class="stat-value">${(display.open_time_limited || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label stat-label--restricted">Permit / restricted</span>
                <span class="stat-value">${(display.restricted || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label stat-label--unknown">Unknown / check signs</span>
                <span class="stat-value">${(display.unknown || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Cambridge active meter points</span>
                <span class="stat-value">${(evidence.cambridge_active_meter_spaces || 0).toLocaleString()}</span>
            </div>
        `;
    } catch (error) {
        console.error('Error loading stats:', error);
        setAppStatus('Coverage statistics could not be loaded.', true);
    }
}

// Clear search results
function clearSearch() {
    setAppStatus('');
    resetHover();
    if (searchResultsLayer) {
        map.removeLayer(searchResultsLayer);
        searchResultsLayer = null;
    }
    refreshSelectionStyles();
    document.getElementById('search-input').value = '';
    if (allStreetsLayer) {
        map.fitBounds(allStreetsLayer.getBounds(), { padding: [20, 20] });
    }
    loadStats(); // Reload original stats
}

// Event listeners
document.getElementById('search-btn').addEventListener('click', () => {
    const query = document.getElementById('search-input').value.trim();
    if (query) {
        searchStreets(query);
    } else {
        clearSearch();
    }
});

document.getElementById('search-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
        document.getElementById('search-btn').click();
    } else if (e.key === 'Escape') {
        clearSearch();
    }
});

document.querySelectorAll('.map-mode-btn').forEach((button) => {
    button.addEventListener('click', () => {
        setBaseLayer(button.dataset.baseLayer || 'dark');
    });
});

map.on('baselayerchange', (event) => {
    const layerName = String(event.name || 'dark').toLowerCase();
    document.querySelectorAll('.map-mode-btn').forEach((button) => {
        const isActive = button.dataset.baseLayer === layerName;
        button.classList.toggle('active', isActive);
        button.setAttribute('aria-pressed', String(isActive));
    });
});

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadStreets();
    setupParkingEvidence();
    loadStats();
});

map.on('mouseout', resetHover);
map.on('zoomend', () => {
    refreshSelectionStyles();
    updateEvidenceRendering();
});
