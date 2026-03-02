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

// Color scheme based on parking permit requirements
// Since 2010: ALL public streets require permits
// Private streets are NOT under city jurisdiction = no permit needed
const COLORS = {
    permitRequired: '#ef4444',    // Red - public streets (permit required)
    noPermitNeeded: '#22c55e',    // Green - private streets (no permit)
    unknown: '#6b7280',           // Gray - unknown ownership
    highlight: '#f59e0b',         // Orange - search results
    hover: '#3b82f6'              // Blue - hover
};

// Get style based on street ownership
function getStreetStyle(feature) {
    const ownership = feature.properties?.OWNERSHIP;
    let color = COLORS.unknown;
    
    if (ownership === 'Public' || ownership === 'State land') {
        color = COLORS.permitRequired;
    } else if (ownership === 'Private') {
        color = COLORS.noPermitNeeded;
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
        'PERMIT_STATUS': 'Permit Status'
    };
    return labels[key] || key;
}

// Get permit status text based on ownership
function getPermitStatus(ownership) {
    if (ownership === 'Public' || ownership === 'State land') {
        return 'Permit Required';
    } else if (ownership === 'Private') {
        return 'No Permit Needed';
    }
    return 'Unknown';
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
    
    // Add permit status as first item
    const permitStatus = getPermitStatus(properties.OWNERSHIP);
    let statusColor = COLORS.unknown;
    if (properties.OWNERSHIP === 'Private') {
        statusColor = COLORS.noPermitNeeded;
    } else if (properties.OWNERSHIP === 'Public' || properties.OWNERSHIP === 'State land') {
        statusColor = COLORS.permitRequired;
    }
    
    let html = `
        <div class="detail-row permit-status">
            <span class="detail-label">${formatLabel('PERMIT_STATUS')}</span>
            <span class="detail-value" style="color: ${statusColor}; font-weight: bold;">${permitStatus}</span>
        </div>
    `;
    
    const displayProps = ['STNAME', 'OWNERSHIP', 'FUNC_CLASS', 'ONEWAY', 'MATERIAL'];
    
    for (const key of displayProps) {
        let value = properties[key];
        if (key === 'ONEWAY') {
            value = formatOneway(value);
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
    const ownership = properties.OWNERSHIP || 'Unknown';
    return `<strong>${name}</strong><br>Ownership: ${ownership}`;
}

// Add interactivity to each feature
function onEachFeature(feature, layer) {
    const baseStyle = getStreetStyle(feature);

    layer.on({
        mouseover: function(e) {
            e.target.setStyle(hoverStyle);
            e.target.bringToFront();
        },
        mouseout: function(e) {
            // Always return to default style after hover
            e.target.setStyle(baseStyle);
        },
        click: function(e) {
            const props = feature.properties;
            showStreetDetails(props);
            e.target.bindPopup(createPopup(props)).openPopup();
            // Prevent hover color from sticking after click/popup interactions
            e.target.setStyle(baseStyle);
        }
    });
}

// Special handler for search results - keeps highlight style
function onEachSearchFeature(feature, layer) {
    const baseStyle = highlightStyle;

    layer.on({
        mouseover: function(e) {
            e.target.setStyle(hoverStyle);
            e.target.bringToFront();
        },
        mouseout: function(e) {
            // Keep highlight style for search results
            e.target.setStyle(baseStyle);
        },
        click: function(e) {
            const props = feature.properties;
            showStreetDetails(props);
            e.target.bindPopup(createPopup(props)).openPopup();
            // Keep highlighted search color after click
            e.target.setStyle(baseStyle);
        }
    });
}

// Load and display all streets
async function loadStreets() {
    try {
        const response = await fetch('/api/streets');
        const data = await response.json();
        
        if (allStreetsLayer) {
            map.removeLayer(allStreetsLayer);
        }
        
        allStreetsLayer = L.geoJSON(data, {
            style: getStreetStyle,
            onEachFeature: onEachFeature
        }).addTo(map);
        
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
        container.innerHTML = `
            <div class="stat-item">
                <span class="stat-label">Total Segments</span>
                <span class="stat-value">${stats.total_segments.toLocaleString()}</span>
            </div>
            <div class="stat-item">
                <span class="stat-label">Unique Streets</span>
                <span class="stat-value">${stats.unique_streets.toLocaleString()}</span>
            </div>
        `;
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

// Clear search results
function clearSearch() {
    if (searchResultsLayer) {
        map.removeLayer(searchResultsLayer);
        searchResultsLayer = null;
    }
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
