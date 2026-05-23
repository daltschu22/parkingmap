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

L.control.layers({
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
        button.classList.toggle('active', button.dataset.baseLayer === layerName);
    });
}

// Color scheme based on parking access without resident pass
const COLORS = {
    meteredNoPass: '#0ea5e9',     // Blue - metered parking
    timeLimitedNoPass: '#22c55e', // Green - timed parking, no resident pass
    residentPermitRequired: '#ef4444', // Red - resident permit required
    segmentRulesKnown: '#f97316',  // Orange - partial/segment rules known
    privateRules: '#a855f7',      // Purple - private street rules
    unknown: '#6b7280',           // Gray - unknown
    highlight: '#f59e0b',         // Orange - search results
    hover: '#3b82f6'              // Blue - hover
};

// Get style based on street ownership
function getStreetStyle(feature) {
    const access = feature.properties?.PARKING_ACCESS;
    let color = COLORS.unknown;
    
    if (access === 'permit_with_metered_segments') {
        color = COLORS.meteredNoPass;
    } else if (access === 'permit_with_time_limited_segments') {
        color = COLORS.timeLimitedNoPass;
    } else if (access === 'resident_permit_required') {
        color = COLORS.residentPermitRequired;
    } else if (access === 'resident_permit_segment_rules_known' || access === 'metered_segments_known' || access === 'parking_special_spaces_known') {
        color = COLORS.segmentRulesKnown;
    } else if (access === 'private_rules_apply') {
        color = COLORS.privateRules;
    }
    
    return {
        color: color,
        weight: 2,
        opacity: 0.8
    };
}

// Style for highlighted/searched streets
const highlightStyle = {
    color: COLORS.highlight,
    weight: 4,
    opacity: 1
};

// Style on hover
const hoverStyle = {
    color: COLORS.hover,
    weight: 5,
    opacity: 1
};

const selectedStyle = {
    color: '#fde047',
    weight: 6,
    opacity: 1
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
        'PARKING_RULE_MATCH_LEVEL': 'Rule Match',
        'PARKING_MEDFORD_RULE_COUNT': 'Medford Rule Rows',
        'PARKING_MEDFORD_PARTIAL_RULE_COUNT': 'Partial Rule Rows',
        'PARKING_MEDFORD_RULE_SUMMARY': 'Medford Rule Summary',
        'PARKING_CAMBRIDGE_METER_COUNT_ESTIMATE': 'Cambridge Meter Spaces',
        'PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE': 'Active Meter Spaces',
        'PARKING_CAMBRIDGE_ACCESSIBLE_SPACE_COUNT': 'Accessible Spaces',
        'PARKING_CAMBRIDGE_METER_HOURS': 'Meter Hours',
        'PARKING_CAMBRIDGE_METER_MAX_TIMES': 'Meter Max Times',
        'PARKING_CAMBRIDGE_METER_RATES': 'Meter Rates',
        'FROM_STREET': 'From Street',
        'TO_STREET': 'To Street',
        'ROAD_TYPE': 'Road Type'
    };
    return labels[key] || key;
}

function getParkingAccessText(access) {
    if (access === 'permit_with_metered_segments') return 'Permit Street (Has Metered Segments)';
    if (access === 'metered_segments_known') return 'Metered Segments Known';
    if (access === 'permit_with_time_limited_segments') return 'Permit Street (Has Time-Limited Segments)';
    if (access === 'resident_permit_required') return 'Resident Permit Required';
    if (access === 'resident_permit_segment_rules_known') return 'Segment-Specific Permit Rules Known';
    if (access === 'parking_special_spaces_known') return 'Special Parking Spaces Known';
    if (access === 'private_rules_apply') return 'Private Street Rules Apply';
    return 'Unknown';
}

function getParkingAccessColor(access) {
    if (access === 'permit_with_metered_segments') return COLORS.meteredNoPass;
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

// Format one-way value
function formatOneway(value) {
    if (value === 'F') return 'No';
    if (value === 'T' || value === 'TF' || value === 'FT') return 'Yes';
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
            <span class="detail-label">${formatLabel('PARKING_ACCESS')}</span>
            <span class="detail-value" style="color: ${statusColor}; font-weight: bold;">${accessText}</span>
        </div>
        <div class="detail-row">
            <span class="detail-label">${formatLabel('PARKING_NOTE')}</span>
            <span class="detail-value">${parkingNote}</span>
        </div>
    `;

    if (meterCount !== null && meterCount !== undefined) {
        html += `
            <div class="detail-row">
                <span class="detail-label">Meter Count Estimate</span>
                <span class="detail-value">${meterCount} (${meterConfidence})</span>
            </div>
        `;
    }
    
    const displayProps = [
        'MUNICIPALITY',
        'STNAME',
        'OWNERSHIP',
        'FUNC_CLASS',
        'ROAD_TYPE',
        'FROM_STREET',
        'TO_STREET',
        'ONEWAY',
        'MATERIAL',
        'PARKING_RULE_MATCH_LEVEL',
        'PARKING_MEDFORD_RULE_COUNT',
        'PARKING_MEDFORD_PARTIAL_RULE_COUNT',
        'PARKING_MEDFORD_RULE_SUMMARY',
        'PARKING_CAMBRIDGE_METER_COUNT_ESTIMATE',
        'PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE',
        'PARKING_CAMBRIDGE_ACCESSIBLE_SPACE_COUNT',
        'PARKING_CAMBRIDGE_METER_HOURS',
        'PARKING_CAMBRIDGE_METER_MAX_TIMES',
        'PARKING_CAMBRIDGE_METER_RATES'
    ];
    
    for (const key of displayProps) {
        let value = properties[key];
        if (key === 'ONEWAY') {
            value = formatOneway(value);
        } else if (key === 'OWNERSHIP') {
            value = getOwnershipText(value);
        }
        if (value) {
            html += `
                <div class="detail-row">
                    <span class="detail-label">${formatLabel(key)}</span>
                    <span class="detail-value">${value}</span>
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
    return `<strong>${name}</strong><br>${municipality}<br>${access}`;
}

function createMeterEvidencePopup(properties) {
    const status = properties.STATUS || 'Unknown status';
    const hours = properties.OPERATION_HOURS || 'Hours unknown';
    const maxTime = properties.MAX_TIME || 'Max time unknown';
    const rate = properties.RATE || 'Rate unknown';
    return `<strong>Cambridge meter space</strong><br>${status}<br>${hours}<br>${maxTime}<br>${rate}`;
}

function createAccessibleEvidencePopup(properties) {
    const street = properties.STNAME || 'Unknown street';
    const side = properties.SIDE_OF_STREET || 'Side unknown';
    return `<strong>Cambridge accessible space</strong><br>${street}<br>${side}`;
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
        resetHover();
        const response = await fetch('/api/streets');
        const data = await response.json();
        
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
    } catch (error) {
        console.error('Error loading streets:', error);
    }
}

async function loadParkingEvidence() {
    try {
        const [meterResponse, accessibleResponse] = await Promise.all([
            fetch('/api/parking-evidence/cambridge/meters'),
            fetch('/api/parking-evidence/cambridge/accessible')
        ]);
        const [meters, accessible] = await Promise.all([
            meterResponse.json(),
            accessibleResponse.json()
        ]);

        if (meterEvidenceLayer) {
            map.removeLayer(meterEvidenceLayer);
        }
        if (accessibleEvidenceLayer) {
            map.removeLayer(accessibleEvidenceLayer);
        }

        meterEvidenceLayer = L.geoJSON(meters, {
            style: {
                color: COLORS.meteredNoPass,
                weight: 1,
                opacity: 0.9,
                fillColor: COLORS.meteredNoPass,
                fillOpacity: 0.45
            },
            onEachFeature: (feature, layer) => {
                layer.bindPopup(createMeterEvidencePopup(feature.properties || {}));
            }
        }).addTo(map);

        accessibleEvidenceLayer = L.geoJSON(accessible, {
            pointToLayer: (feature, latlng) => L.circleMarker(latlng, {
                radius: 4,
                color: '#fde047',
                weight: 2,
                fillColor: '#facc15',
                fillOpacity: 0.95
            }),
            onEachFeature: (feature, layer) => {
                layer.bindPopup(createAccessibleEvidencePopup(feature.properties || {}));
            }
        }).addTo(map);

        console.log(`Loaded ${meters.features?.length || 0} Cambridge meter spaces and ${accessible.features?.length || 0} accessible spaces`);
    } catch (error) {
        console.error('Error loading parking evidence:', error);
    }
}

// Search streets by name
async function searchStreets(query) {
    try {
        resetHover();
        const response = await fetch(`/api/streets/search?q=${encodeURIComponent(query)}`);
        const data = await response.json();
        
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
        } else {
            alert(`No streets found matching "${query}"`);
            console.log('No streets found matching query');
        }
    } catch (error) {
        console.error('Error searching streets:', error);
    }
}

// Load statistics
async function loadStats() {
    try {
        const response = await fetch('/api/stats');
        const stats = await response.json();
        
        const container = document.getElementById('stats-content');
        const metered = stats.parking_access?.permit_with_metered_segments || 0;
        const knownMetered = stats.parking_access?.metered_segments_known || 0;
        const timeLimited = stats.parking_access?.permit_with_time_limited_segments || 0;
        const permitRequired = stats.parking_access?.resident_permit_required || 0;
        const segmentRules = stats.parking_access?.resident_permit_segment_rules_known || 0;
        const specialSpaces = stats.parking_access?.parking_special_spaces_known || 0;
        const municipalities = Object.entries(stats.municipalities || {})
            .map(([name, count]) => `${name}: ${count.toLocaleString()}`)
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
                <span class="stat-label">Metered Segments Known</span>
                <span class="stat-value">${(metered + knownMetered).toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Permit + Time-Limited Segments</span>
                <span class="stat-value">${timeLimited.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Permit Required</span>
                <span class="stat-value">${permitRequired.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Segment Rules Known</span>
                <span class="stat-value">${(segmentRules + specialSpaces).toLocaleString()}</span>
            </div>
        `;
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// Clear search results
function clearSearch() {
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
        button.classList.toggle('active', button.dataset.baseLayer === layerName);
    });
});

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadStreets();
    loadParkingEvidence();
    loadStats();
});

map.on('mouseout', resetHover);
