// Somerville Parking Map - Main JavaScript

// Initialize map centered on Somerville, MA
const map = L.map('map').setView([42.3876, -71.0995], 14);

// Add dark tile layer
L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19
}).addTo(map);

// Layer groups for streets
let allStreetsLayer = null;
let searchResultsLayer = null;
let hoveredStreetName = null;
let selectedStreetName = null;

// Color scheme based on parking access without resident pass
const COLORS = {
    meteredNoPass: '#0ea5e9',     // Blue - metered parking
    timeLimitedNoPass: '#22c55e', // Green - timed parking, no resident pass
    residentPermitRequired: '#ef4444', // Red - resident permit required
    privateRules: '#a855f7',      // Purple - private street rules
    unknown: '#6b7280',           // Gray - unknown
    highlight: '#f59e0b',         // Orange - search results
    hover: '#3b82f6'              // Blue - hover
};

// Get style based on street ownership
function getStreetStyle(feature) {
    const access = feature.properties?.PARKING_ACCESS;
    let color = COLORS.unknown;
    
    if (access === 'metered_no_pass') {
        color = COLORS.meteredNoPass;
    } else if (access === 'time_limited_no_pass') {
        color = COLORS.timeLimitedNoPass;
    } else if (access === 'resident_permit_required') {
        color = COLORS.residentPermitRequired;
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
    return String(feature?.properties?.STNAME || '').trim().toUpperCase();
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
    const streetKey = String(streetName || '').trim().toUpperCase();
    if (!streetKey) return;

    if (hoveredStreetName && hoveredStreetName !== streetKey) {
        resetHover();
    }

    hoveredStreetName = streetKey;
    forEachStreetLayer((layer) => {
        if (getStreetKey(layer.feature) === streetKey) {
            layer.setStyle(hoverStyle);
            layer.bringToFront();
        }
    });
}

// Format property labels for display
function formatLabel(key) {
    const labels = {
        'STNAME': 'Street Name',
        'ONEWAY': 'One Way',
        'OWNERSHIP': 'Ownership',
        'FUNC_CLASS': 'Road Class',
        'MATERIAL': 'Material',
        'ROW_WIDTH': 'ROW Width',
        'PAVE_WIDTH': 'Pave Width',
        'PARKING_ACCESS': 'Parking Access',
        'PARKING_NOTE': 'Parking Note'
    };
    return labels[key] || key;
}

function getParkingAccessText(access) {
    if (access === 'metered_no_pass') return 'Metered (No Resident Pass)';
    if (access === 'time_limited_no_pass') return 'Time Limited (No Resident Pass)';
    if (access === 'resident_permit_required') return 'Resident Permit Required';
    if (access === 'private_rules_apply') return 'Private Street Rules Apply';
    return 'Unknown';
}

function getParkingAccessColor(access) {
    if (access === 'metered_no_pass') return COLORS.meteredNoPass;
    if (access === 'time_limited_no_pass') return COLORS.timeLimitedNoPass;
    if (access === 'resident_permit_required') return COLORS.residentPermitRequired;
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
    
    const displayProps = ['STNAME', 'OWNERSHIP', 'FUNC_CLASS', 'ONEWAY', 'MATERIAL'];
    
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
    const access = getParkingAccessText(properties.PARKING_ACCESS);
    return `<strong>${name}</strong><br>${access}`;
}

// Add interactivity to each feature
function onEachFeature(feature, layer) {
    layer._isSearchResult = false;

    layer.on({
        mouseover: function(e) {
            applyHoverStreet(getStreetKey(e.target.feature));
        },
        mouseout: function(e) {
            if (hoveredStreetName === getStreetKey(e.target.feature)) {
                resetHover();
            }
        },
        click: function(e) {
            const props = feature.properties;
            selectStreet(props?.STNAME);
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
            if (hoveredStreetName === getStreetKey(e.target.feature)) {
                resetHover();
            }
        },
        click: function(e) {
            const props = feature.properties;
            selectStreet(props?.STNAME);
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
                onEachFeature: onEachSearchFeature
            }).addTo(map);
            refreshSelectionStyles();
            
            // Fit map to search results
            map.fitBounds(searchResultsLayer.getBounds(), { padding: [50, 50] });
            
            // Update stats to show search results
            const uniqueNames = new Set(data.features.map(f => f.properties?.STNAME).filter(Boolean));
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
        const metered = stats.parking_access?.metered_no_pass || 0;
        const timeLimited = stats.parking_access?.time_limited_no_pass || 0;
        const permitRequired = stats.parking_access?.resident_permit_required || 0;

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
                <span class="stat-label">Metered (No Pass)</span>
                <span class="stat-value">${metered.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Time Limited (No Pass)</span>
                <span class="stat-value">${timeLimited.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Permit Required</span>
                <span class="stat-value">${permitRequired.toLocaleString()}</span>
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

// Initialize
document.addEventListener('DOMContentLoaded', () => {
    loadStreets();
    loadStats();
});

map.on('mouseout', resetHover);
