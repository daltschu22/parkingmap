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

// Color scheme based on parking access without resident pass
const COLORS = {
    meteredNoPass: '#0ea5e9',     // Blue - metered parking
    inactiveMeterEvidence: '#64748b', // Slate - inactive/removed/proposed meters
    timeLimitedNoPass: '#22c55e', // Green - timed parking, no resident pass
    residentPermitRequired: '#ef4444', // Red - resident permit required
    segmentRulesKnown: '#f97316',  // Orange - partial/segment rules known
    privateRules: '#a855f7',      // Purple - private street rules
    unknown: '#6b7280',           // Gray - unknown
    cambridgeNetwork: '#cbd5e1',  // Neutral - Cambridge network, rules not yet mapped
    highlight: '#f59e0b',         // Orange - search results
    hover: '#3b82f6'              // Blue - hover
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

// Get style based on street ownership
function getStreetStyle(feature) {
    const access = feature.properties?.PARKING_ACCESS;
    const municipality = String(feature.properties?.MUNICIPALITY || '').trim().toLowerCase();
    let color = COLORS.unknown;
    let dashArray = null;

    if (municipality === 'cambridge' && access !== 'private_rules_apply') {
        return {
            color: COLORS.cambridgeNetwork,
            weight: 1.75,
            opacity: 0.62,
            dashArray: null
        };
    }
    
    if (access === 'permit_with_metered_segments') {
        color = COLORS.meteredNoPass;
    } else if (access === 'inactive_metered_segments_known') {
        color = COLORS.inactiveMeterEvidence;
        dashArray = '3 5';
    } else if (access === 'permit_with_time_limited_segments') {
        color = COLORS.timeLimitedNoPass;
    } else if (access === 'resident_permit_required') {
        color = COLORS.residentPermitRequired;
    } else if (access === 'resident_permit_segment_rules_known' || access === 'metered_segments_known' || access === 'parking_special_spaces_known') {
        color = COLORS.segmentRulesKnown;
    } else if (access === 'private_rules_apply') {
        color = COLORS.privateRules;
        dashArray = '8 4';
    } else {
        dashArray = '2 5';
    }
    
    return {
        color: color,
        weight: 2,
        opacity: 0.8,
        dashArray
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

function getParkingAccessText(access) {
    if (access === 'permit_with_metered_segments') return 'Permit Street (Has Metered Segments)';
    if (access === 'metered_segments_known') return 'Metered Segments Known';
    if (access === 'inactive_metered_segments_known') return 'Inactive/Proposed Meter Evidence Only';
    if (access === 'permit_with_time_limited_segments') return 'Permit Street (Has Time-Limited Segments)';
    if (access === 'resident_permit_required') return 'Resident Permit Required';
    if (access === 'resident_permit_segment_rules_known') return 'Segment-Specific Permit Rules Known';
    if (access === 'parking_special_spaces_known') return 'Special Parking Spaces Known';
    if (access === 'private_rules_apply') return 'Private Street Rules Apply';
    return 'Unknown';
}

function getParkingAccessColor(access) {
    if (access === 'permit_with_metered_segments') return COLORS.meteredNoPass;
    if (access === 'inactive_metered_segments_known') return COLORS.inactiveMeterEvidence;
    if (access === 'permit_with_time_limited_segments') return COLORS.timeLimitedNoPass;
    if (access === 'resident_permit_required') return COLORS.residentPermitRequired;
    if (access === 'resident_permit_segment_rules_known' || access === 'metered_segments_known' || access === 'parking_special_spaces_known') return COLORS.segmentRulesKnown;
    if (access === 'private_rules_apply') return COLORS.privateRules;
    return COLORS.unknown;
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
    
    const parkingAccess = properties.PARKING_ACCESS || 'unknown';
    const accessText = getParkingAccessText(parkingAccess);
    const statusColor = getParkingAccessColor(parkingAccess);
    const parkingNote = properties.PARKING_NOTE || 'No additional parking rule note available.';
    const meterCount = properties.PARKING_METER_COUNT_ESTIMATE;
    const meterConfidence = properties.PARKING_METER_COUNT_CONFIDENCE || 'none';
    
    let html = `
        <div class="detail-row permit-status">
            <span class="detail-label">${escapeHtml(formatLabel('PARKING_ACCESS'))}</span>
            <span class="detail-value" style="color: ${statusColor}; font-weight: bold;">${escapeHtml(accessText)}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">${escapeHtml(formatLabel('PARKING_NOTE'))}</span>
            <span class="detail-value">${escapeHtml(parkingNote)}</span>
        </div>
    `;

    if (meterCount !== null && meterCount !== undefined) {
        html += `
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
            html += `
                <div class="detail-row">
                    <span class="detail-label">${escapeHtml(formatLabel(key))}</span>
                    <span class="detail-value">${renderedValue}</span>
                </div>
            `;
        }
    }
    
    container.innerHTML = html || '<p class="info-hint">No details available</p>';
}

// Create popup content
function createPopup(properties) {
    const name = properties.STNAME || 'Unknown Street';
    const municipality = properties.MUNICIPALITY || 'Unknown';
    const access = getParkingAccessText(properties.PARKING_ACCESS);
    const evidence = municipality === 'Cambridge'
        ? `<br>${escapeHtml(getParkingEvidenceText(properties.PARKING_EVIDENCE))}`
        : '';
    return `<strong>${escapeHtml(name)}</strong><br>${escapeHtml(municipality)}<br>${escapeHtml(access)}${evidence}`;
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

function evidenceLayerDefinitions() {
    return [
        {
            key: 'meters',
            label: 'Cambridge meter spaces',
            url: '/api/parking-evidence/cambridge/meters',
            group: meterEvidenceLayer,
            options: {
                renderer: hitRenderer,
                style: (feature) => {
                    const active = String(feature.properties?.STATUS || '').trim().toLowerCase() === 'in service';
                    const color = active ? COLORS.meteredNoPass : COLORS.inactiveMeterEvidence;
                    return { color, weight: 1, opacity: 0.9, fillColor: color, fillOpacity: 0.45 };
                },
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
                pointToLayer: (_feature, latlng) => L.circleMarker(latlng, {
                    radius: 4,
                    color: '#fde047',
                    weight: 2,
                    fillColor: '#facc15',
                    fillOpacity: 0.95
                }),
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
}

function addMeterCentroidMarkers(group, geoJsonLayer) {
    geoJsonLayer.eachLayer((layer) => {
        if (typeof layer.getBounds !== 'function') return;
        const bounds = layer.getBounds();
        if (!bounds.isValid()) return;
        const properties = layer.feature?.properties || {};
        const active = String(properties.STATUS || '').trim().toLowerCase() === 'in service';
        const color = active ? COLORS.meteredNoPass : COLORS.inactiveMeterEvidence;
        L.circleMarker(bounds.getCenter(), {
            renderer: hitRenderer,
            radius: active ? 3 : 2.5,
            color,
            weight: 1,
            opacity: 0.95,
            fillColor: color,
            fillOpacity: 0.9
        }).bindPopup(createMeterEvidencePopup(properties)).addTo(group);
    });
}

async function ensureEvidenceLayer(definition) {
    if (loadedEvidenceLayers.has(definition.key)) return;
    if (evidenceLoadPromises.has(definition.key)) {
        return evidenceLoadPromises.get(definition.key);
    }

    setAppStatus(`Loading ${definition.label}…`);
    const loadPromise = fetchJson(definition.url)
        .then((data) => {
            definition.group.clearLayers();
            const geoJsonLayer = L.geoJSON(data, definition.options);
            definition.group.addLayer(geoJsonLayer);
            if (definition.key === 'meters') {
                addMeterCentroidMarkers(definition.group, geoJsonLayer);
            }
            loadedEvidenceLayers.add(definition.key);
            setAppStatus(`Loaded ${data.features?.length || 0} ${definition.label.toLowerCase()}.`);
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
        const metered = stats.parking_access?.permit_with_metered_segments || 0;
        const timeLimited = stats.parking_access?.permit_with_time_limited_segments || 0;
        const permitRequired = stats.parking_access?.resident_permit_required || 0;
        const segmentRules = stats.parking_access?.resident_permit_segment_rules_known || 0;
        const evidence = stats.parking_evidence || {};
        const municipalities = Object.entries(stats.municipalities || {})
            .map(([name, count]) => `${escapeHtml(name)}: ${count.toLocaleString()}`)
            .join(' / ');

        container.innerHTML = `
            <div class="stat-item">
                <span class="stat-label">Total Segments</span>
                <span class="stat-value">${stats.total_segments.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Unique Streets</span>
                <span class="stat-value">${stats.unique_streets.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Municipalities</span>
                <span class="stat-value">${municipalities}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Cambridge Meter Spaces</span>
                <span class="stat-value">${(evidence.cambridge_meter_spaces || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Active Cambridge Meters</span>
                <span class="stat-value">${(evidence.cambridge_active_meter_spaces || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Cambridge Accessible Spaces</span>
                <span class="stat-value">${(evidence.cambridge_accessible_spaces || 0).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Somerville Metered Segments</span>
                <span class="stat-value">${metered.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Somerville Time-Limited Segments</span>
                <span class="stat-value">${timeLimited.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Permit Required</span>
                <span class="stat-value">${permitRequired.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Medford Partial Rules</span>
                <span class="stat-value">${segmentRules.toLocaleString()}</span>
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
