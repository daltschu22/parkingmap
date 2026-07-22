// Driver-first parking map interactions.

const DEFAULT_MAP_VIEW = { center: [42.397, -71.107], zoom: 13 };
const MOBILE_BREAKPOINT = 860;
const map = L.map('map', { preferCanvas: true, zoomControl: true })
    .setView(DEFAULT_MAP_VIEW.center, DEFAULT_MAP_VIEW.zoom);
// A forgiving hit target keeps thin street lines easy to tap without making them look heavy.
const hitRenderer = L.canvas({ tolerance: 10 });

const lightBaseLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19
});
const darkBaseLayer = L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/attributions">CARTO</a>',
    subdomains: 'abcd',
    maxZoom: 19
});
const satelliteBaseLayer = L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
    attribution: 'Tiles &copy; Esri',
    maxZoom: 19
});
const baseLayers = { light: lightBaseLayer, dark: darkBaseLayer, satellite: satelliteBaseLayer };
let activeBaseLayer = lightBaseLayer;
activeBaseLayer.addTo(map);

const COLORS = {
    metered: '#0284c7',
    openTimeLimited: '#059669',
    restricted: '#e11d48',
    unknown: '#64748b',
    inactiveMeterEvidence: '#64748b',
    highlight: '#d97706',
    hover: '#0284c7',
    selected: '#0ea5e9'
};

let allStreetsLayer = null;
let meterEvidenceLayer = null;
let accessibleEvidenceLayer = null;
let publicParkingFacilityLayer = null;
let evidenceDefinitions = [];
let selectedStreetLayer = null;
let selectedStreetName = null;
let hoveredStreetName = null;
let hoverResetTimer = null;
let searchRequestVersion = 0;
let userLocationLayer = null;
const searchResultStreetKeys = new Set();
const evidenceLoadPromises = new Map();
const loadedEvidenceLayers = new Set();

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

function fetchJson(url) {
    return fetch(url, { headers: { Accept: 'application/json' } }).then((response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return response.json();
    });
}

function isMobile() {
    return window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT}px)`).matches;
}

function setPanelExpanded(expanded) {
    const panel = document.getElementById('app-panel');
    const toggle = document.getElementById('sheet-toggle');
    if (!panel) return;
    panel.classList.toggle('sheet-collapsed', !expanded);
    document.body.classList.toggle('panel-expanded', expanded);
    toggle?.setAttribute('aria-expanded', String(expanded));
    window.setTimeout(() => map.invalidateSize({ pan: false }), 220);
}

function showSelectionPanel() {
    const empty = document.getElementById('empty-state');
    const close = document.getElementById('clear-selection-btn');
    if (empty) empty.hidden = true;
    if (close) close.hidden = false;
    if (isMobile()) setPanelExpanded(true);
}

function showEmptyState() {
    const empty = document.getElementById('empty-state');
    const close = document.getElementById('clear-selection-btn');
    const details = document.getElementById('street-details');
    if (empty) empty.hidden = false;
    if (close) close.hidden = true;
    if (details) details.innerHTML = '';
}

function setAppStatus(message = '', isError = false) {
    const status = document.getElementById('app-status');
    if (!status) return;
    status.textContent = message;
    status.classList.toggle('error', isError);
}

function setBaseLayer(layerName) {
    const nextLayer = baseLayers[layerName] || lightBaseLayer;
    if (activeBaseLayer !== nextLayer) {
        map.removeLayer(activeBaseLayer);
        nextLayer.addTo(map);
        nextLayer.bringToBack();
        activeBaseLayer = nextLayer;
    }
    document.querySelectorAll('.map-mode-btn').forEach((button) => {
        const active = button.dataset.baseLayer === layerName;
        button.classList.toggle('active', active);
        button.setAttribute('aria-pressed', String(active));
    });
}

function getFeatureDisplayStatus(properties = {}) {
    if (properties.PARKING_DISPLAY_STATUS) return properties.PARKING_DISPLAY_STATUS;
    const access = properties.PARKING_ACCESS || 'unknown';
    if ([
        'permit_with_metered_segments',
        'permit_with_time_limited_segments',
        'resident_permit_required',
        'resident_permit_time_restricted',
        'private_rules_apply'
    ].includes(access)) return 'restricted';
    return 'unknown';
}

function getStreetStyle(feature) {
    const properties = feature.properties || {};
    const municipality = String(properties.MUNICIPALITY || '').trim().toLowerCase();
    const access = properties.PARKING_ACCESS || 'unknown';
    const displayStatus = getFeatureDisplayStatus(properties);
    const detailed = map.getZoom() >= 15;
    const weight = detailed ? 2.1 : 1.15;

    // Somerville's permit rule is a citywide baseline, not proof about both curbs
    // on every rendered centerline. Keep those lines neutral and explain on selection.
    const somervilleBaseline = municipality === 'somerville' && [
        'permit_with_metered_segments',
        'permit_with_time_limited_segments',
        'resident_permit_required',
        'resident_permit_segment_rules_known'
    ].includes(access);
    if (somervilleBaseline) {
        return { color: COLORS.unknown, weight: detailed ? 1.6 : 0.8, opacity: 0.48, dashArray: null };
    }
    if (displayStatus === 'metered') {
        return { color: COLORS.metered, weight, opacity: 0.88, dashArray: null };
    }
    if (displayStatus === 'open_time_limited') {
        return { color: COLORS.openTimeLimited, weight, opacity: 0.88, dashArray: null };
    }
    if (displayStatus === 'restricted') {
        return { color: COLORS.restricted, weight, opacity: 0.88, dashArray: '7 5' };
    }
    return {
        color: COLORS.unknown,
        weight: detailed ? 1.5 : 0.7,
        opacity: municipality === 'cambridge' ? 0.4 : 0.46,
        dashArray: null
    };
}

const highlightStyle = { color: COLORS.highlight, weight: 2.5, opacity: 0.9, dashArray: '8 6' };
const hoverStyle = { color: COLORS.hover, weight: 3.5, opacity: 1, dashArray: null };
const selectedStyle = { color: COLORS.selected, weight: 4.5, opacity: 1, dashArray: null };

function getStreetKey(feature) {
    const props = feature?.properties || {};
    const municipality = String(props.MUNICIPALITY || '').trim().toUpperCase();
    const street = String(props.STNAME || '').trim().toUpperCase();
    return street ? `${municipality}:${street}` : '';
}

function forEachStreetLayer(callback) {
    allStreetsLayer?.eachLayer((layer) => {
        if (layer?.feature) callback(layer);
    });
}

function applyLayerRestStyle(layer) {
    if (layer === selectedStreetLayer) {
        layer.setStyle(selectedStyle);
    } else if (searchResultStreetKeys.has(getStreetKey(layer.feature))) {
        layer.setStyle(highlightStyle);
    } else {
        layer.setStyle(getStreetStyle(layer.feature));
    }
}

function refreshSelectionStyles() {
    forEachStreetLayer(applyLayerRestStyle);
}

function selectStreet(key, layer = null) {
    selectedStreetName = String(key || '').trim().toUpperCase() || null;
    selectedStreetLayer = layer;
    resetHover();
    refreshSelectionStyles();
}

function clearStreetSelection({ collapseMobile = false } = {}) {
    selectedStreetName = null;
    selectedStreetLayer = null;
    showEmptyState();
    refreshSelectionStyles();
    if (collapseMobile && isMobile()) setPanelExpanded(false);
}

function resetHover() {
    if (hoverResetTimer) window.clearTimeout(hoverResetTimer);
    hoverResetTimer = null;
    if (!hoveredStreetName) return;
    const previous = hoveredStreetName;
    hoveredStreetName = null;
    forEachStreetLayer((layer) => {
        if (getStreetKey(layer.feature) === previous) applyLayerRestStyle(layer);
    });
}

function applyHoverStreet(key) {
    const streetKey = String(key || '').trim().toUpperCase();
    if (!streetKey || streetKey === hoveredStreetName) return;
    resetHover();
    hoveredStreetName = streetKey;
    forEachStreetLayer((layer) => {
        if (getStreetKey(layer.feature) === streetKey && layer !== selectedStreetLayer) {
            layer.setStyle(hoverStyle);
        }
    });
}

function scheduleHoverReset(key) {
    const streetKey = String(key || '').trim().toUpperCase();
    if (!streetKey || hoveredStreetName !== streetKey) return;
    if (hoverResetTimer) window.clearTimeout(hoverResetTimer);
    hoverResetTimer = window.setTimeout(resetHover, 50);
}

function getAtGlanceAnswer(properties = {}) {
    const municipality = String(properties.MUNICIPALITY || '').trim().toLowerCase();
    const access = properties.PARKING_ACCESS || 'unknown';
    const evidence = properties.PARKING_EVIDENCE || 'none';
    const displayStatus = getFeatureDisplayStatus(properties);

    if (municipality === 'cambridge' && evidence === 'active_meter_spaces_nearby') {
        return {
            tone: 'caution', title: 'Use a mapped meter',
            summary: 'Blue meter shapes are confirmed spaces nearby. This street line does not confirm the rest of the curb.'
        };
    }
    if (municipality === 'cambridge' && evidence === 'accessible_spaces_nearby') {
        return {
            tone: 'caution', title: 'Accessible space mapped nearby',
            summary: 'Use the exact accessible-space marker. General parking on the surrounding curb is not confirmed.'
        };
    }
    if (municipality === 'cambridge' && evidence === 'inactive_meter_spaces_nearby') {
        return {
            tone: 'caution', title: 'No active meter confirmed',
            summary: 'Nearby meter records are inactive, removed, or proposed. Check the posted curb signs.'
        };
    }
    if (municipality === 'somerville' && access === 'permit_with_metered_segments') {
        return {
            tone: 'caution', title: 'Permit or marked meter',
            summary: 'A resident permit is the baseline on City streets. Metered exceptions exist, but their exact curb locations are not in the source.'
        };
    }
    if (municipality === 'somerville' && access === 'permit_with_time_limited_segments') {
        return {
            tone: 'caution', title: 'Permit or posted time limit',
            summary: 'A resident permit is the baseline. Public time-limited exceptions exist, but their exact curb locations are not mapped.'
        };
    }
    if (municipality === 'somerville' && access === 'resident_permit_required') {
        return {
            tone: 'restricted', title: 'Resident permit is the baseline',
            summary: 'Treat this as permit parking unless a sign or marked space says otherwise. The line is not an exact curb rule.'
        };
    }
    if (access === 'permit_with_metered_segments') {
        return {
            tone: 'caution', title: 'Permit or marked meter',
            summary: 'Without a permit, use only a clearly marked meter. Exact meter blocks are not fully mapped here.'
        };
    }
    if (access === 'permit_with_time_limited_segments') {
        return {
            tone: 'caution', title: 'Permit or posted time limit',
            summary: 'Without a permit, use only a clearly posted public space. Exact exception blocks are not fully mapped.'
        };
    }
    if (access === 'resident_permit_segment_rules_known') {
        return {
            tone: 'caution', title: 'Rules vary by block',
            summary: 'A permit restriction exists somewhere on this street, but this segment was not matched confidently.'
        };
    }
    if (access === 'resident_permit_time_restricted') {
        return {
            tone: 'restricted', title: 'Permit required during posted hours',
            summary: 'A scheduled permit restriction is documented for this segment. Posted signs remain the final authority.'
        };
    }
    if (displayStatus === 'metered') {
        return {
            tone: 'caution', title: 'Use a marked meter',
            summary: 'Metered sections exist. Other portions of the street can have different rules.'
        };
    }
    if (displayStatus === 'open_time_limited') {
        return {
            tone: 'allowed', title: 'Public parking is documented',
            summary: 'A public or time-limited section exists. Confirm the hours and exact curb on posted signs.'
        };
    }
    if (access === 'private_rules_apply') {
        return {
            tone: 'restricted', title: 'Private street — permission required',
            summary: 'Do not assume public parking. Follow property-owner signs and authorization.'
        };
    }
    if (displayStatus === 'restricted') {
        return {
            tone: 'restricted', title: 'Permit restriction documented',
            summary: 'Without the required permit, do not assume parking is allowed. Posted exceptions still control.'
        };
    }
    return {
        tone: 'caution', title: 'We can’t confirm this curb',
        summary: 'The current source does not answer for this exact curb. Check posted signs before parking.'
    };
}

function formatDate(value) {
    if (!value) return '';
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return String(value);
    return new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric' }).format(date);
}

function formatOneway(value) {
    const normalized = String(value ?? '').trim().toUpperCase();
    if (normalized === 'F' || normalized === '0') return 'No';
    if (['T', 'TF', 'FT', '1', '-1'].includes(normalized)) return 'Yes';
    return value || 'Unknown';
}

function getParkingEvidenceText(value) {
    const labels = {
        active_meter_spaces_nearby: 'Active meter spaces nearby',
        inactive_meter_spaces_nearby: 'Inactive, removed, or proposed meters nearby',
        accessible_spaces_nearby: 'Accessible spaces nearby',
        none: 'No mapped point evidence nearby'
    };
    return labels[String(value || '').trim()] || value || 'None';
}

function renderFacts(facts) {
    return `<div class="answer-facts">${facts.map(([label, value]) => `
        <div class="answer-fact">
            <span class="answer-fact-label">${escapeHtml(label)}</span>
            <span class="answer-fact-value" title="${escapeHtml(value)}">${escapeHtml(value)}</span>
        </div>`).join('')}</div>`;
}

function renderDetailRows(rows) {
    return rows.filter(([, value]) => value !== null && value !== undefined && value !== '').map(([label, value, url]) => {
        const safeUrl = safeExternalUrl(url);
        const rendered = safeUrl
            ? `<a href="${escapeHtml(safeUrl)}" target="_blank" rel="noopener noreferrer">${escapeHtml(value)}</a>`
            : escapeHtml(value);
        return `<div class="detail-row"><span class="detail-label">${escapeHtml(label)}</span><span class="detail-value">${rendered}</span></div>`;
    }).join('');
}

function streetFacts(properties, answer) {
    const access = String(properties.PARKING_ACCESS || 'unknown');
    const permit = access.includes('permit')
        ? (access.includes('time_restricted') ? 'Scheduled' : 'Baseline / likely')
        : (access === 'private_rules_apply' ? 'Private rules' : 'Not confirmed');
    const evidence = String(properties.PARKING_EVIDENCE || 'none');
    const payment = evidence === 'active_meter_spaces_nearby' || access.includes('metered')
        ? 'At marked meter'
        : (answer.tone === 'allowed' ? 'See signs' : 'Not confirmed');
    const certainty = properties.PARKING_RULE_MATCH_LEVEL === 'segment'
        ? 'Segment matched'
        : 'Street-level only';
    return [['Permit', permit], ['Payment', payment], ['Curb certainty', certainty]];
}

function showStreetDetails(properties = {}) {
    const container = document.getElementById('street-details');
    if (!container) return;
    const answer = getAtGlanceAnswer(properties);
    const sourceUrl = properties.PARKING_SOURCE_URL;
    const evidenceUrl = properties.PARKING_EVIDENCE_SOURCE_URL;
    const generated = formatDate(properties.PARKING_DATA_UPDATED_AT);
    const rows = [
        ['Rule note', properties.PARKING_NOTE || 'No additional rule note is available.'],
        ['Rule source', properties.PARKING_RULE_SOURCE, sourceUrl],
        ['Rule confidence', properties.PARKING_CONFIDENCE],
        ['Rule match', properties.PARKING_RULE_MATCH_LEVEL],
        ['Mapped evidence', getParkingEvidenceText(properties.PARKING_EVIDENCE)],
        ['Evidence source', properties.PARKING_EVIDENCE_SOURCE, evidenceUrl],
        ['Evidence confidence', properties.PARKING_EVIDENCE_CONFIDENCE],
        ['From / to', [properties.FROM_STREET, properties.TO_STREET].filter(Boolean).join(' to ')],
        ['Ownership', properties.OWNERSHIP],
        ['One way', formatOneway(properties.ONEWAY)],
        ['Documented meter spaces', properties.PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE],
        ['Meter hours', properties.PARKING_CAMBRIDGE_METER_HOURS],
        ['Meter maximum', properties.PARKING_CAMBRIDGE_METER_MAX_TIMES],
        ['Meter rates', properties.PARKING_CAMBRIDGE_METER_RATES],
        ['Medford rule summary', properties.PARKING_MEDFORD_RULE_SUMMARY]
    ];
    container.innerHTML = `
        <article class="parking-answer parking-answer--${escapeHtml(answer.tone)}">
            <p class="answer-eyebrow">Can I park here?</p>
            <p class="answer-location">${escapeHtml(properties.STNAME || 'Unknown street')} · ${escapeHtml(properties.MUNICIPALITY || 'Unknown city')}</p>
            <h3 class="answer-title">${escapeHtml(answer.title)}</h3>
            <p class="answer-summary">${escapeHtml(answer.summary)}</p>
            ${renderFacts(streetFacts(properties, answer))}
            ${generated ? `<p class="answer-freshness">Data generated ${escapeHtml(generated)}. Posted curb signs always take priority.</p>` : '<p class="answer-freshness">Posted curb signs always take priority.</p>'}
        </article>
        <details class="answer-details">
            <summary>Why this answer?</summary>
            <div class="answer-detail-body">${renderDetailRows(rows)}</div>
        </details>`;
    showSelectionPanel();
}

function formatMeterRate(value) {
    const raw = String(value || '').trim().replace(/^\$/, '');
    const numeric = Number(raw);
    return raw && Number.isFinite(numeric) ? `$${numeric.toFixed(2)}/hour` : (value || 'Unknown');
}

function showMeterDetails(properties = {}) {
    const container = document.getElementById('street-details');
    if (!container) return;
    const status = properties.STATUS || 'Unknown';
    const active = isActiveMeter(properties);
    const street = properties.MATCHED_STREET || properties.NEAREST_STREET || 'Cambridge';
    const matchDistance = Number(properties.MATCH_DISTANCE_METERS);
    const rows = [
        ['Payment zone', properties.PAY_BY_PHONE_ZONE],
        ['Source last edited', formatDate(properties.LAST_EDITED_DATE)],
        ['Nearest street match', Number.isFinite(matchDistance) ? `${street} · ${matchDistance.toFixed(1)} m (approximate)` : street],
        ['Source', 'Cambridge GIS traffic data', 'https://github.com/cambridgegis/cambridgegis_data/tree/main/Traffic']
    ];
    container.innerHTML = `
        <article class="parking-answer parking-answer--${active ? 'allowed' : 'caution'}">
            <p class="answer-eyebrow">Mapped meter space</p>
            <p class="answer-location">${escapeHtml(street)} · Cambridge</p>
            <h3 class="answer-title">${escapeHtml(active ? 'Meter is in service' : status)}</h3>
            <p class="answer-summary">This shape represents a specific meter space, not the whole street. Confirm the meter display and curb signs.</p>
            ${renderFacts([['Hours', properties.OPERATION_HOURS || 'Unknown'], ['Rate', formatMeterRate(properties.RATE)], ['Maximum', properties.MAX_TIME || 'Unknown']])}
            <p class="answer-freshness">Availability and meter status are not live.</p>
        </article>
        <details class="answer-details"><summary>Meter details</summary><div class="answer-detail-body">${renderDetailRows(rows)}</div></details>`;
    selectedStreetLayer = null;
    refreshSelectionStyles();
    showSelectionPanel();
}

function showAccessibleDetails(properties = {}) {
    const container = document.getElementById('street-details');
    if (!container) return;
    const address = `${properties.STREET_NUMBER ? `${properties.STREET_NUMBER} ` : ''}${properties.STNAME || 'Unknown street'}`;
    const anchors = [properties.FROM_STREET, properties.TO_STREET].filter(Boolean).join(' to ');
    container.innerHTML = `
        <article class="parking-answer parking-answer--allowed">
            <p class="answer-eyebrow">Mapped accessible space</p>
            <p class="answer-location">${escapeHtml(address)} · Cambridge</p>
            <h3 class="answer-title">Placard or plate required</h3>
            <p class="answer-summary">This marker represents a specific public accessible parking space. Check the sign and curb before parking.</p>
            ${renderFacts([['Side', properties.SIDE_OF_STREET || 'Unknown'], ['Location', anchors || 'See marker'], ['Payment', 'Check signs']])}
            <p class="answer-freshness">Source edited ${escapeHtml(formatDate(properties.LAST_EDITED_DATE) || 'date unavailable')}.</p>
        </article>`;
    selectedStreetLayer = null;
    refreshSelectionStyles();
    showSelectionPanel();
}

function showFacilityDetails(feature = {}) {
    const properties = feature.properties || {};
    const container = document.getElementById('street-details');
    if (!container) return;
    const coordinates = feature.geometry?.coordinates || [];
    const longitude = Number(coordinates[0]);
    const latitude = Number(coordinates[1]);
    const directions = Number.isFinite(latitude) && Number.isFinite(longitude)
        ? `https://www.google.com/maps/dir/?api=1&destination=${encodeURIComponent(`${latitude},${longitude}`)}`
        : '';
    const source = safeExternalUrl(properties.DETAILS_URL || properties.SOURCE_URL);
    const spaces = properties.TOTAL_SPACES !== null && properties.TOTAL_SPACES !== undefined
        ? String(properties.TOTAL_SPACES)
        : 'Not listed';
    const rows = [
        ['Parking rules', properties.PARKING_REGULATIONS],
        ['Public access', properties.PUBLIC_ACCESS],
        ['Operator', properties.OPERATOR],
        ['Accessible parking', properties.ACCESSIBLE_PARKING],
        ['EV charging', properties.EV_CHARGING],
        ['Special restrictions', properties.SPECIAL_RESTRICTIONS],
        ['Snow emergency', properties.SNOW_EMERGENCY_PARKING],
        ['Source confidence', properties.SOURCE_CONFIDENCE]
    ];
    container.innerHTML = `
        <article class="parking-answer parking-answer--facility">
            <p class="answer-eyebrow">Public parking location</p>
            <p class="answer-location">${escapeHtml(properties.ADDRESS || properties.MUNICIPALITY || '')}</p>
            <h3 class="answer-title">${escapeHtml(properties.NAME || 'Public parking facility')}</h3>
            <p class="answer-summary">${escapeHtml(properties.PARKING_REGULATIONS || properties.PUBLIC_ACCESS || 'Check posted facility rules before parking.')}</p>
            ${renderFacts([['Type', properties.FACILITY_TYPE || 'Parking'], ['Spaces', spaces], ['Access', properties.OWNERSHIP_TYPE || 'Public']])}
            <p class="answer-freshness">Space availability is not live.</p>
            <div class="answer-actions">
                ${directions ? `<a class="answer-action answer-action--primary" href="${escapeHtml(directions)}" target="_blank" rel="noopener noreferrer">Directions</a>` : ''}
                ${source ? `<a class="answer-action" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">Official details</a>` : ''}
            </div>
        </article>
        <details class="answer-details"><summary>Facility details</summary><div class="answer-detail-body">${renderDetailRows(rows)}</div></details>`;
    selectedStreetLayer = null;
    refreshSelectionStyles();
    showSelectionPanel();
}

function onEachStreet(feature, layer) {
    const properties = feature.properties || {};
    const label = `${properties.STNAME || 'Unknown street'} · ${properties.MUNICIPALITY || 'Unknown city'}`;
    layer.bindTooltip(escapeHtml(label), { sticky: true, className: 'street-tooltip', direction: 'top' });
    layer.on({
        mouseover: (event) => applyHoverStreet(getStreetKey(event.target.feature)),
        mouseout: (event) => scheduleHoverReset(getStreetKey(event.target.feature)),
        click: (event) => {
            selectStreet(getStreetKey(feature), event.target);
            showStreetDetails(properties);
            resetHover();
            applyLayerRestStyle(event.target);
        }
    });
}

async function loadStreets() {
    try {
        setAppStatus('Loading streets…');
        const data = await fetchJson('/api/streets');
        if (allStreetsLayer) map.removeLayer(allStreetsLayer);
        allStreetsLayer = L.geoJSON(data, {
            style: getStreetStyle,
            renderer: hitRenderer,
            onEachFeature: onEachStreet
        }).addTo(map);
        refreshSelectionStyles();
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
        weight: detailed ? 1 : 0,
        opacity: detailed ? 0.9 : 0,
        fillColor: color,
        fillOpacity: detailed ? 0.52 : 0,
        interactive: detailed
    };
}

function createFacilityCluster() {
    if (typeof L.markerClusterGroup !== 'function') return L.layerGroup();
    return L.markerClusterGroup({
        showCoverageOnHover: false,
        maxClusterRadius: 44,
        disableClusteringAtZoom: 16,
        spiderfyOnMaxZoom: true,
        chunkedLoading: true
    });
}

function makeEvidenceDefinitions() {
    return [
        {
            key: 'meters',
            label: 'Cambridge meter spaces',
            url: '/api/parking-evidence/cambridge/meters',
            group: meterEvidenceLayer,
            options: {
                renderer: hitRenderer,
                filter: (feature) => isActiveMeter(feature.properties || {}),
                style: (feature) => meterEvidenceStyle(feature.properties || {}),
                onEachFeature: (feature, layer) => layer.on('click', () => showMeterDetails(feature.properties || {}))
            }
        },
        {
            key: 'public-facilities',
            label: 'Public lots and garages',
            url: '/api/parking-evidence/public-facilities',
            group: publicParkingFacilityLayer,
            clustered: true,
            options: {
                pointToLayer: (feature, latlng) => L.marker(latlng, {
                    title: `${feature.properties?.NAME || 'Public parking'} — ${feature.properties?.MUNICIPALITY || ''}`,
                    icon: L.divIcon({
                        className: 'facility-marker',
                        html: '<span class="facility-marker-inner" aria-hidden="true">P</span>',
                        iconSize: [28, 28],
                        iconAnchor: [14, 14]
                    })
                }),
                onEachFeature: (feature, layer) => layer.on('click', () => showFacilityDetails(feature))
            }
        },
        {
            key: 'accessible',
            label: 'Cambridge accessible spaces',
            url: '/api/parking-evidence/cambridge/accessible',
            group: accessibleEvidenceLayer,
            options: {
                pointToLayer: (feature, latlng) => L.marker(latlng, {
                    title: `Accessible parking — ${feature.properties?.STNAME || 'Cambridge'}`,
                    icon: L.divIcon({
                        className: 'accessible-marker',
                        html: '<span class="accessible-marker-inner" aria-hidden="true">♿</span>',
                        iconSize: [22, 22],
                        iconAnchor: [11, 11]
                    })
                }),
                onEachFeature: (feature, layer) => layer.on('click', () => showAccessibleDetails(feature.properties || {}))
            }
        }
    ];
}

async function ensureEvidenceLayer(definition) {
    if (loadedEvidenceLayers.has(definition.key)) return;
    if (evidenceLoadPromises.has(definition.key)) return evidenceLoadPromises.get(definition.key);
    const loadPromise = fetchJson(definition.url)
        .then((data) => {
            definition.group.clearLayers();
            const geoJsonLayer = L.geoJSON(data, definition.options);
            if (definition.clustered && typeof definition.group.addLayers === 'function') {
                definition.group.addLayers(geoJsonLayer.getLayers());
            } else {
                definition.group.addLayer(geoJsonLayer);
            }
            loadedEvidenceLayers.add(definition.key);
            updateEvidenceRendering();
        })
        .catch((error) => {
            console.error(`Error loading ${definition.label}:`, error);
            if (map.hasLayer(definition.group)) map.removeLayer(definition.group);
            const input = document.querySelector(`[data-evidence-layer="${definition.key}"]`);
            if (input) input.checked = false;
            setAppStatus(`${definition.label} could not be loaded.`, true);
            updateMapEvidenceKey();
        })
        .finally(() => evidenceLoadPromises.delete(definition.key));
    evidenceLoadPromises.set(definition.key, loadPromise);
    return loadPromise;
}

function updateActiveLayerCount() {
    const count = [...document.querySelectorAll('[data-evidence-layer]')].filter((input) => input.checked).length;
    const label = document.getElementById('active-layer-count');
    if (label) label.textContent = `${count} on`;
}

function updateMapEvidenceKey() {
    const container = document.getElementById('map-evidence-key-items');
    const control = document.querySelector('.map-evidence-key');
    if (!container || !control) return;
    const items = [
        ['public-facilities', '<i class="map-evidence-key-swatch map-evidence-key-swatch--facility"></i>', 'Public parking'],
        ['meters', '<i class="map-evidence-key-swatch map-evidence-key-swatch--meter"></i>', 'Meters · zoom in'],
        ['accessible', '<span aria-hidden="true">♿</span>', 'Accessible · zoom in']
    ].filter(([key]) => document.querySelector(`[data-evidence-layer="${key}"]`)?.checked);
    control.hidden = items.length === 0;
    container.innerHTML = items.map(([, symbol, label]) => `<span class="map-evidence-key-row">${symbol}<span>${escapeHtml(label)}</span></span>`).join('');
    updateActiveLayerCount();
}

function setupMapEvidenceKey() {
    const control = L.control({ position: 'bottomleft' });
    control.onAdd = () => {
        const container = L.DomUtil.create('div', 'map-evidence-key');
        container.setAttribute('role', 'note');
        container.innerHTML = '<strong>Visible parking locations</strong><span id="map-evidence-key-items"></span>';
        L.DomEvent.disableClickPropagation(container);
        return container;
    };
    control.addTo(map);
    updateMapEvidenceKey();
}

function updateEvidenceRendering() {
    const detailed = map.getZoom() >= 15;
    meterEvidenceLayer?.eachLayer((layer) => {
        if (typeof layer.eachLayer !== 'function') return;
        layer.eachLayer((shape) => {
            if (shape.feature && typeof shape.setStyle === 'function') {
                const style = meterEvidenceStyle(shape.feature.properties || {});
                shape.setStyle(style);
                shape.options.interactive = style.interactive;
            }
        });
    });
    accessibleEvidenceLayer?.eachLayer((layer) => {
        if (typeof layer.eachLayer !== 'function') return;
        layer.eachLayer((marker) => {
            if (typeof marker.setOpacity === 'function') marker.setOpacity(detailed ? 1 : 0);
            if (marker.getElement()) marker.getElement().style.pointerEvents = detailed ? '' : 'none';
        });
    });
}

function setupParkingEvidence() {
    meterEvidenceLayer = L.layerGroup();
    accessibleEvidenceLayer = L.layerGroup();
    publicParkingFacilityLayer = createFacilityCluster();
    evidenceDefinitions = makeEvidenceDefinitions();
    setupMapEvidenceKey();

    document.querySelectorAll('[data-evidence-layer]').forEach((input) => {
        input.addEventListener('change', () => {
            const definition = evidenceDefinitions.find((item) => item.key === input.dataset.evidenceLayer);
            if (!definition) return;
            if (input.checked) {
                definition.group.addTo(map);
                void ensureEvidenceLayer(definition);
            } else {
                map.removeLayer(definition.group);
            }
            updateMapEvidenceKey();
        });
    });

    evidenceDefinitions.forEach((definition) => {
        const input = document.querySelector(`[data-evidence-layer="${definition.key}"]`);
        if (input?.checked) {
            definition.group.addTo(map);
            void ensureEvidenceLayer(definition);
        }
    });
}

function groupSearchResults(features = []) {
    const grouped = new Map();
    features.forEach((feature) => {
        const key = getStreetKey(feature);
        if (!key) return;
        if (!grouped.has(key)) grouped.set(key, { key, feature, count: 0, layers: [] });
        grouped.get(key).count += 1;
    });
    forEachStreetLayer((layer) => {
        const result = grouped.get(getStreetKey(layer.feature));
        if (result) result.layers.push(layer);
    });
    return [...grouped.values()].sort((a, b) => {
        const city = String(a.feature.properties?.MUNICIPALITY || '').localeCompare(String(b.feature.properties?.MUNICIPALITY || ''));
        return city || String(a.feature.properties?.STNAME || '').localeCompare(String(b.feature.properties?.STNAME || ''));
    });
}

function focusSearchResult(result) {
    if (!result) return;
    const bounds = L.latLngBounds([]);
    result.layers.forEach((layer) => {
        if (typeof layer.getBounds === 'function') bounds.extend(layer.getBounds());
    });
    if (bounds.isValid()) map.fitBounds(bounds, { padding: [55, 55], maxZoom: 17 });
    const selectedLayer = result.layers[0] || null;
    selectStreet(result.key, selectedLayer);
    showStreetDetails(result.feature.properties || {});
    const searchResults = document.getElementById('search-results');
    if (searchResults) searchResults.hidden = true;
}

function renderSearchResults(results) {
    const container = document.getElementById('search-results');
    if (!container) return;
    if (results.length <= 1) {
        container.hidden = true;
        container.innerHTML = '';
        return;
    }
    container.innerHTML = results.slice(0, 8).map((result, index) => {
        const properties = result.feature.properties || {};
        return `<button type="button" class="search-result-button" data-result-index="${index}">
            <span><span class="search-result-name">${escapeHtml(properties.STNAME || 'Unknown street')}</span><span class="search-result-city">${escapeHtml(properties.MUNICIPALITY || '')}</span></span>
            <span class="search-result-count">${result.count} segment${result.count === 1 ? '' : 's'}</span>
        </button>`;
    }).join('');
    container.hidden = false;
    container.querySelectorAll('[data-result-index]').forEach((button) => {
        button.addEventListener('click', () => focusSearchResult(results[Number(button.dataset.resultIndex)]));
    });
}

async function searchStreets(query) {
    const requestVersion = ++searchRequestVersion;
    try {
        setAppStatus(`Searching for ${query}…`);
        resetHover();
        searchResultStreetKeys.clear();
        selectedStreetLayer = null;
        selectedStreetName = null;
        showEmptyState();
        const data = await fetchJson(`/api/streets/search?q=${encodeURIComponent(query)}`);
        if (requestVersion !== searchRequestVersion) return;
        const features = data.features || [];
        if (!features.length) {
            refreshSelectionStyles();
            renderSearchResults([]);
            setAppStatus(`No streets found matching “${query}”.`, true);
            if (isMobile()) setPanelExpanded(true);
            return;
        }
        features.forEach((feature) => {
            const key = getStreetKey(feature);
            if (key) searchResultStreetKeys.add(key);
        });
        refreshSelectionStyles();
        const results = groupSearchResults(features);
        renderSearchResults(results);
        const allBounds = L.latLngBounds([]);
        results.flatMap((result) => result.layers).forEach((layer) => {
            if (typeof layer.getBounds === 'function') allBounds.extend(layer.getBounds());
        });
        if (allBounds.isValid()) map.fitBounds(allBounds, { padding: [55, 55], maxZoom: 17 });
        if (results.length === 1) focusSearchResult(results[0]);
        else if (isMobile()) setPanelExpanded(true);
        setAppStatus(`${results.length} matching street${results.length === 1 ? '' : 's'} · ${features.length} mapped segments`);
    } catch (error) {
        if (requestVersion !== searchRequestVersion) return;
        console.error('Error searching streets:', error);
        setAppStatus('Street search failed. Please retry.', true);
    }
}

async function loadStats() {
    try {
        const stats = await fetchJson('/api/stats');
        const container = document.getElementById('stats-content');
        if (!container) return;
        const evidence = stats.parking_evidence || {};
        container.innerHTML = [
            [stats.total_segments || 0, 'Mapped road segments'],
            [evidence.cambridge_active_meter_spaces || 0, 'Exact active meter spaces'],
            [evidence.public_parking_facilities || 0, 'Public lots / garages'],
            [evidence.cambridge_accessible_spaces || 0, 'Accessible spaces']
        ].map(([value, label]) => `<div class="stat-card"><span class="stat-value">${Number(value).toLocaleString()}</span><span class="stat-label">${escapeHtml(label)}</span></div>`).join('');
    } catch (error) {
        console.error('Error loading stats:', error);
    }
}

function clearSearch({ resetView = true } = {}) {
    searchRequestVersion += 1;
    setAppStatus('');
    resetHover();
    searchResultStreetKeys.clear();
    clearStreetSelection();
    const input = document.getElementById('search-input');
    const clearButton = document.getElementById('clear-search-btn');
    const results = document.getElementById('search-results');
    if (input) input.value = '';
    if (clearButton) clearButton.hidden = true;
    if (results) {
        results.hidden = true;
        results.innerHTML = '';
    }
    refreshSelectionStyles();
    if (resetView) map.setView(DEFAULT_MAP_VIEW.center, DEFAULT_MAP_VIEW.zoom);
}

function locateUser() {
    const button = document.getElementById('locate-btn');
    button?.setAttribute('aria-busy', 'true');
    setAppStatus('Finding your location…');
    map.locate({ enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
}

map.on('locationfound', (event) => {
    document.getElementById('locate-btn')?.removeAttribute('aria-busy');
    if (userLocationLayer) map.removeLayer(userLocationLayer);
    const marker = L.marker(event.latlng, {
        title: 'Your location',
        icon: L.divIcon({ className: '', html: '<span class="user-location-marker"></span>', iconSize: [16, 16], iconAnchor: [8, 8] })
    });
    const accuracy = L.circle(event.latlng, {
        radius: Math.min(event.accuracy || 0, 300),
        color: '#2563eb', weight: 1, opacity: 0.55, fillColor: '#60a5fa', fillOpacity: 0.13,
        interactive: false
    });
    userLocationLayer = L.layerGroup([accuracy, marker]).addTo(map);
    map.setView(event.latlng, Math.max(map.getZoom(), 16));
    setAppStatus('Location found. Tap the nearest curb segment.');
    if (isMobile()) setPanelExpanded(false);
});

map.on('locationerror', (event) => {
    document.getElementById('locate-btn')?.removeAttribute('aria-busy');
    const denied = event.code === 1;
    setAppStatus(denied ? 'Location access was blocked. Search for a street instead.' : 'Your location could not be found. Try a street search.', true);
    if (isMobile()) setPanelExpanded(true);
});

function setupControls() {
    const input = document.getElementById('search-input');
    const clearButton = document.getElementById('clear-search-btn');
    document.getElementById('search-btn')?.addEventListener('click', () => {
        const query = input?.value.trim() || '';
        if (query) void searchStreets(query);
        else input?.focus();
    });
    input?.addEventListener('input', () => {
        if (clearButton) clearButton.hidden = !input.value;
    });
    input?.addEventListener('keydown', (event) => {
        if (event.key === 'Enter') document.getElementById('search-btn')?.click();
        if (event.key === 'Escape') clearSearch();
    });
    clearButton?.addEventListener('click', () => {
        clearSearch();
        input?.focus();
    });
    document.getElementById('locate-btn')?.addEventListener('click', locateUser);
    document.getElementById('clear-selection-btn')?.addEventListener('click', () => clearStreetSelection({ collapseMobile: true }));
    document.getElementById('sheet-toggle')?.addEventListener('click', () => {
        const collapsed = document.getElementById('app-panel')?.classList.contains('sheet-collapsed');
        setPanelExpanded(Boolean(collapsed));
    });
    document.getElementById('map-panel-button')?.addEventListener('click', () => {
        const tools = document.getElementById('map-tools');
        if (tools) tools.open = true;
        setPanelExpanded(true);
        window.setTimeout(() => tools?.scrollIntoView({ block: 'nearest' }), 230);
    });
    document.querySelectorAll('.map-mode-btn').forEach((button) => {
        button.addEventListener('click', () => setBaseLayer(button.dataset.baseLayer || 'light'));
    });
}

document.addEventListener('DOMContentLoaded', () => {
    setupControls();
    if (isMobile()) setPanelExpanded(false);
    else setPanelExpanded(true);
    void loadStreets();
    setupParkingEvidence();
    void loadStats();
});

map.on('mouseout', resetHover);
map.on('zoomend', () => {
    refreshSelectionStyles();
    updateEvidenceRendering();
});
