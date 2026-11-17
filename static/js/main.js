// Driver-first parking map interactions.

const DEFAULT_MAP_VIEW = { center: [42.3967, -71.1218], zoom: 16 };
const MOBILE_BREAKPOINT = 860;
const map = L.map('map', { preferCanvas: true, zoomControl: true })
    .setView(DEFAULT_MAP_VIEW.center, DEFAULT_MAP_VIEW.zoom);
// A forgiving hit target keeps thin street lines easy to tap without making them look heavy.
map.createPane('streetContext').style.zIndex = 360;
const hitRenderer = L.canvas({ pane: 'streetContext', tolerance: 10 });

const streetTileUrl = 'https://tile.openstreetmap.org/{z}/{x}/{y}.png';
const streetTileOptions = {
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>',
    maxZoom: 19
};
const lightBaseLayer = L.tileLayer(streetTileUrl, { ...streetTileOptions, className: 'light-street-tiles' });
const darkBaseLayer = L.tileLayer(streetTileUrl, {
    ...streetTileOptions,
    className: 'dark-street-tiles'
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
let streetLoad = null;
let meterEvidenceLayer = null;
let accessibleEvidenceLayer = null;
let publicParkingFacilityLayer = null;
let evidenceDefinitions = [];
let selectedStreetLayer = null;
let refreshTimedDetail = null;
let selectedStreetName = null;
let hoveredStreetName = null;
let hoverResetTimer = null;
let searchRequestVersion = 0;
let detailRequestVersion = 0;
const streetLayersById = new Map();
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
    if (!value) return '';
    try {
        const url = new URL(String(value || ''), window.location.origin);
        return url.protocol === 'https:' ? url.href : '';
    } catch (_error) {
        return '';
    }
}

function fetchJson(url) {
    const versioned = new URL(url, window.location.origin);
    versioned.searchParams.set('v', document.documentElement.dataset.appVersion);
    return fetch(versioned, { headers: { Accept: 'application/json' } }).then((response) => {
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
    if (!expanded && isMobile()) {
        const editor = document.getElementById('arrival-editor');
        if (document.activeElement?.closest('#sidebar-body, #arrival-form')) toggle.focus({preventScroll: true});
        editor.open = false;
    }
    panel.classList.toggle('sheet-collapsed', !expanded);
    document.body.classList.toggle('panel-expanded', expanded);
    toggle?.setAttribute('aria-expanded', String(expanded));
    document.getElementById('sheet-toggle-label').textContent = expanded ? 'Show map' : 'Show parking results';
}

function setupSheetToggle() {
    const toggle = document.getElementById('sheet-toggle');
    const results = document.getElementById('sidebar-body');
    let start = null;
    let dragged = false;
    toggle.addEventListener('pointerdown', event => {
        if (!event.isPrimary || event.button !== 0) return;
        start = {id: event.pointerId, x: event.clientX, y: event.clientY};
        dragged = false;
        toggle.setPointerCapture(event.pointerId);
    });
    toggle.addEventListener('pointerup', event => {
        if (start?.id !== event.pointerId) return;
        const dx = Math.abs(event.clientX - start.x);
        const dy = event.clientY - start.y;
        dragged = Math.max(dx, Math.abs(dy)) > 10;
        if (Math.abs(dy) >= 28 && Math.abs(dy) > dx) setPanelExpanded(dy < 0);
        start = null;
    });
    toggle.addEventListener('lostpointercapture', () => { start = null; });
    toggle.addEventListener('click', event => {
        if (!dragged || event.detail === 0) setPanelExpanded(toggle.getAttribute('aria-expanded') !== 'true');
        dragged = false;
    });

    // Pulling down from the top of the list also dismisses the sheet. A swipe
    // that started partway down the list remains an ordinary scroll.
    let pull = null;
    results.addEventListener('touchstart', event => {
        pull = isMobile() && results.scrollTop <= 0 && event.touches.length === 1 ? event.touches[0] : null;
    }, {passive: true});
    results.addEventListener('touchcancel', () => { pull = null; }, {passive: true});
    results.addEventListener('touchend', event => {
        const end = event.changedTouches[0];
        if (pull && end.identifier === pull.identifier && results.scrollTop <= 0) {
            const dy = end.clientY - pull.clientY;
            if (dy > 55 && dy > Math.abs(end.clientX - pull.clientX)) {
                if (event.cancelable) event.preventDefault();
                setPanelExpanded(false);
            }
        }
        pull = null;
    });
}

function showSelectionPanel(withNearby = false, expand = true) {
    refreshTimedDetail = null;
    document.getElementById('parking-heading').textContent = 'Parking details';
    const empty = document.getElementById('empty-state');
    const close = document.getElementById('clear-selection-btn');
    if (empty) empty.hidden = !withNearby;
    document.getElementById('nearby-heading').hidden = !withNearby;
    if (withNearby) renderVisitorChoices();
    if (close) close.hidden = false;
    if (expand && isMobile()) setPanelExpanded(true);
}

function showEmptyState() {
    refreshTimedDetail = null;
    document.getElementById('nearby-heading').hidden = true;
    document.getElementById('parking-heading').textContent = 'Visitor parking nearby';
    detailRequestVersion++;
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
    // on every rendered centerline. The dashed line denotes the baseline only.
    const somervilleBaseline = municipality === 'somerville' && [
        'permit_with_metered_segments',
        'permit_with_time_limited_segments',
        'resident_permit_required',
        'resident_permit_segment_rules_known'
    ].includes(access);
    if (properties.SUNDAY_PERMIT_EXCEPTION && arrivalClock().day === 0) {
        return {color: COLORS.unknown, weight: 1, opacity: 0.35, dashArray: null};
    }
    if (somervilleBaseline) {
        return { color: '#a16207', weight: detailed ? 2.2 : 1.25, opacity: 0.7, dashArray: '3 4' };
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
    if (!allStreetsLayer) return;
    const visible = document.getElementById('street-context').checked || searchMode === 'street' ||
        searchResultStreetKeys.size > 0 || Boolean(selectedStreetLayer);
    if (!visible) {
        if (map.hasLayer(allStreetsLayer)) map.removeLayer(allStreetsLayer);
        return;
    }
    if (!map.hasLayer(allStreetsLayer)) allStreetsLayer.addTo(map);
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

    if (properties.SUNDAY_PERMIT_EXCEPTION && arrivalClock().day === 0) {
        return {tone: 'caution', title: 'Sunday permit exception',
            summary: 'The resident-permit requirement is suspended on the selected Sunday. Check the signs for loading, no-parking areas and other limits on this curb.'};
    }
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
            summary: 'A resident permit is the baseline on City streets. Check the marked meter and its hours. The regulation rows below describe locations along this street.'
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
            summary: 'Somerville suspends permit enforcement on Sundays and holidays. At other times, follow the posted permit and visitor rules. The line is not an exact curb rule.'
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
    const permit = properties.SUNDAY_PERMIT_EXCEPTION ? 'Exempt on Sundays' : access.includes('permit')
        ? (access.includes('time_restricted') ? 'Scheduled' : 'Baseline / likely')
        : (access === 'private_rules_apply' ? 'Private rules' : 'Not confirmed');
    const evidence = String(properties.PARKING_EVIDENCE || 'none');
    const payment = evidence === 'active_meter_spaces_nearby' || access.includes('metered')
        ? 'At marked meter'
        : (answer.tone === 'allowed' ? 'See signs' : 'Not confirmed');
    const match = properties.PARKING_RULE_MATCH_LEVEL;
    let certainty = 'Street-level only';
    if (match === 'segment_endpoint_match') certainty = 'Block matched';
    if (!match || match === 'none' || match === 'unresolved_source_row') certainty = 'Unresolved';
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
        ...(properties.SUNDAY_PERMIT_EXCEPTION && properties.MUNICIPALITY === 'Somerville' ?
            [['Sunday exception', 'Somerville Parking Department', 'https://www.somervillema.gov/departments/parking-department']] : []),
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
        ['Active meters matched to this block', properties.PARKING_CAMBRIDGE_ACTIVE_METER_COUNT_ESTIMATE],
        ['Distance to centerline', properties.PARKING_CAMBRIDGE_MATCH_DISTANCE],
        ['Meter hours', properties.PARKING_CAMBRIDGE_METER_HOURS],
        ['Meter maximum', properties.PARKING_CAMBRIDGE_METER_MAX_TIMES],
        ['Meter rates', properties.PARKING_CAMBRIDGE_METER_RATES],
        ['Medford rule summary', properties.PARKING_MEDFORD_RULE_SUMMARY]
    ];
    const sourceRows = (properties.SCHEDULE_ROWS || []).map((row) => [
        `Schedule ${row.schedule} · p. ${row.page}`,
        [row.location, row.side, row.from, row.to, row.restriction].filter(Boolean).join(' · '),
        `${sourceUrl}#page=${row.page}`
    ]);
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
            ${sourceRows.length ? `<details><summary>Regulation rows for this street (${sourceRows.length})</summary>${renderDetailRows(sourceRows)}</details>` : ''}
        </details>`;
    showSelectionPanel(true, false);
    refreshTimedDetail = () => showStreetDetails(properties);
}

function formatMeterRate(value) {
    const raw = String(value || '').trim().replace(/^\$/, '');
    const numeric = Number(raw);
    return raw && Number.isFinite(numeric) ? `$${numeric.toFixed(2)}/hour` : (value || 'Unknown');
}

function showMeterDetails(properties = {}, expand = true) {
    detailRequestVersion++;
    const container = document.getElementById('street-details');
    if (!container) return;
    const status = properties.STATUS || 'Unknown';
    const active = isActiveMeter(properties);
    const feeWaived = noMeterFee({kind: 'meter', city: 'Cambridge', meter_schedule: properties.PAYMENT_SCHEDULE}, arrivalClock());
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
            ${renderFacts([['Hours', properties.OPERATION_HOURS || 'Unknown'], ['Rate', feeWaived ? 'No fee at arrival' : formatMeterRate(properties.RATE)], ['Posted maximum', properties.MAX_TIME || 'Unknown']])}
            <p class="answer-freshness">Availability and meter status are not live.</p>
        </article>
        <details class="answer-details"><summary>Meter details</summary><div class="answer-detail-body">${renderDetailRows(rows)}</div></details>`;
    selectedStreetLayer = null;
    refreshSelectionStyles();
    showSelectionPanel(false, expand);
    refreshTimedDetail = () => showMeterDetails(properties, false);
}

function showAccessibleDetails(properties = {}) {
    detailRequestVersion++;
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
    detailRequestVersion++;
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

async function openSegment(id, key) {
    const version = ++detailRequestVersion;
    selectStreet(key, streetLayersById.get(id));
    const container = document.getElementById('street-details');
    container.textContent = 'Loading block details…';
    showSelectionPanel();
    try {
        const properties = await fetchJson(`/api/streets/segments/${encodeURIComponent(id)}`);
        if (version === detailRequestVersion) showStreetDetails(properties);
    } catch (error) {
        if (version === detailRequestVersion) container.textContent = 'Block details could not be loaded. Tap the block to retry.';
    }
}

function onEachStreet(feature, layer) {
    streetLayersById.set(feature.id, layer);
    const properties = feature.properties || {};
    const label = `${properties.STNAME || 'Unknown street'} · ${properties.MUNICIPALITY || 'Unknown city'}`;
    layer.bindTooltip(escapeHtml(label), { sticky: true, className: 'street-tooltip', direction: 'top' });
    layer.on({
        mouseover: (event) => applyHoverStreet(getStreetKey(event.target.feature)),
        mouseout: (event) => scheduleHoverReset(getStreetKey(event.target.feature)),
        click: () => {
            searchRequestVersion++;
            setAppStatus('');
            void openSegment(feature.id, getStreetKey(feature));
        }
    });
}

function loadStreets() {
    if (allStreetsLayer || streetLoad) return streetLoad;
    streetLoad = fetchJson('/api/streets?view=map').then(data => {
        allStreetsLayer = L.geoJSON(data, {
            style: getStreetStyle,
            renderer: hitRenderer,
            onEachFeature: onEachStreet
        });
        refreshSelectionStyles();
    }).catch(error => {
        console.error('Error loading streets:', error);
        setAppStatus('Street lines could not load. Try the street layer again.', true);
    }).finally(() => { streetLoad = null; });
    return streetLoad;
}

function isActiveMeter(properties = {}) {
    return String(properties.STATUS || '').trim().toLowerCase() === 'in service';
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
            minZoom: 15,
            label: 'Cambridge meter spaces',
            url: '/api/parking-evidence/cambridge/meters?active_only=true',
            group: meterEvidenceLayer,
            options: {
                renderer: hitRenderer,
                style: { color: COLORS.metered, weight: 1, opacity: 0.9, fillOpacity: 0.52 },
                onEachFeature: (feature, layer) => layer.on('click', () => showMeterDetails(feature.properties || {}))
            }
        },
        {
            key: 'public-facilities',
            minZoom: 16,
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
            minZoom: 15,
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
    const count = [...document.querySelectorAll('[data-evidence-layer], #street-context')].filter((input) => input.checked).length;
    const label = document.getElementById('active-layer-count');
    if (label) label.textContent = `${count} on`;
}

function updateMapEvidenceKey() {
    const container = document.getElementById('map-evidence-key-items');
    const control = document.querySelector('.map-evidence-key');
    if (!container || !control) return;
    container.innerHTML = '<span class="map-evidence-key-row"><i class="visitor-section-key"></i>Approximate signed stretch</span>' +
        '<span class="map-evidence-key-row"><i class="visitor-reference-key"></i>Address or intersection reference</span>' +
        '<span class="map-evidence-key-row"><i class="visitor-space-key"></i>Mapped space or facility</span>';
    updateActiveLayerCount();
}

function setupMapEvidenceKey() {
    const control = L.control({ position: 'bottomleft' });
    control.onAdd = () => {
        const container = L.DomUtil.create('div', 'map-evidence-key');
        container.setAttribute('role', 'note');
        container.innerHTML = '<strong>Visitor parking</strong><span id="map-evidence-key-items"></span><p id="map-no-options" hidden>No mapped options in this view.</p><button type="button" id="map-nearest-parking" class="nearest-option" hidden>Show nearest mapped parking</button>';
        L.DomEvent.disableClickPropagation(container);
        return container;
    };
    control.addTo(map);
    updateMapEvidenceKey();
}

function updateEvidenceRendering() {
    for (const definition of evidenceDefinitions) {
        const input = document.querySelector(`[data-evidence-layer="${definition.key}"]`);
        if (input?.checked && visitorLayerVisible(definition.key) && map.getZoom() >= (definition.minZoom || 0)) {
            if (!map.hasLayer(definition.group)) definition.group.addTo(map);
            void ensureEvidenceLayer(definition);
        } else if (map.hasLayer(definition.group)) {
            map.removeLayer(definition.group);
        }
    }
}

function setupParkingEvidence() {
    meterEvidenceLayer = L.layerGroup();
    accessibleEvidenceLayer = L.layerGroup();
    publicParkingFacilityLayer = createFacilityCluster();
    evidenceDefinitions = makeEvidenceDefinitions();
    setupMapEvidenceKey();

    document.querySelectorAll('[data-evidence-layer]').forEach((input) => {
        input.addEventListener('change', () => {
            updateEvidenceRendering();
            updateMapEvidenceKey();
        });
    });

    updateEvidenceRendering();
}

function searchBounds(result) {
    const [west, south, east, north] = result.bounds;
    return L.latLngBounds([south, west], [north, east]);
}

function focusSearchResult(result, recenter = true) {
    if (recenter) map.fitBounds(searchBounds(result), { padding: [55, 55], maxZoom: 17 });
    const key = `${result.municipality}:${result.name}`.toUpperCase();
    searchResultStreetKeys.clear();
    searchResultStreetKeys.add(key);
    if (result.segment_ids.length === 1) {
        void openSegment(result.segment_ids[0], key);
    } else {
        detailRequestVersion++;
        selectStreet(key);
        const title = result.mixed ? 'Rules vary by block' : 'Choose your block';
        const sundayExempt = arrivalClock().day === 0 && result.segment_ids.every(id =>
            streetLayersById.get(id)?.feature.properties.SUNDAY_PERMIT_EXCEPTION);
        const context = sundayExempt ? 'The resident-permit requirement is suspended on this Sunday. Check each curb for other restrictions.' : result.common_access && result.municipality === 'Somerville'
            ? getAtGlanceAnswer({ MUNICIPALITY: result.municipality, PARKING_ACCESS: result.common_access }).title
            : '';
        document.getElementById('street-details').innerHTML = `
            <article class="parking-answer parking-answer--caution">
                <p class="answer-location">${escapeHtml(result.name)} · ${escapeHtml(result.municipality)}</p>
                <h3 class="answer-title">${title}</h3>
                ${context ? `<p class="answer-summary">${escapeHtml(context)}</p>` : ''}
                <p class="answer-summary">Tap your block on the map to see its parking rules and sources.</p>
            </article>`;
        showSelectionPanel(true, recenter);
        refreshTimedDetail = () => focusSearchResult(result, false);
    }
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
        return `<button type="button" class="search-result-button" data-result-index="${index}">
            <span><span class="search-result-name">${escapeHtml(result.name)}</span><span class="search-result-city">${escapeHtml(result.municipality)}</span></span>
            <span class="search-result-count">${result.segment_ids.length} segment${result.segment_ids.length === 1 ? '' : 's'}</span>
        </button>`;
    }).join('');
    container.hidden = false;
    container.querySelectorAll('[data-result-index]').forEach((button) => {
        button.addEventListener('click', () => focusSearchResult(results[Number(button.dataset.resultIndex)]));
    });
}

async function searchStreets(query, fromDestination = false) {
    const requestVersion = ++searchRequestVersion;
    try {
        setAppStatus(`Searching for ${query}…`);
        resetHover();
        searchResultStreetKeys.clear();
        selectedStreetLayer = null;
        selectedStreetName = null;
        showEmptyState();
        if (fromDestination) document.getElementById('empty-state').hidden = true;
        const [data] = await Promise.all([
            fetchJson(`/api/streets/search?view=summary&q=${encodeURIComponent(query)}`), loadStreets()
        ]);
        if (requestVersion !== searchRequestVersion) return;
        const results = data.results.sort((a, b) => a.municipality.localeCompare(b.municipality) || a.name.localeCompare(b.name));
        if (!results.length) {
            refreshSelectionStyles();
            renderSearchResults([]);
            setAppStatus(fromDestination ? 'No destination found. Try a street number and city in Cambridge, Medford, or Somerville.' :
                `No streets found matching “${query}”.`, true);
            if (isMobile()) setPanelExpanded(true);
            return;
        }
        for (const result of results) {
            searchResultStreetKeys.add(`${result.municipality}:${result.name}`.toUpperCase());
        }
        refreshSelectionStyles();
        renderSearchResults(results);
        if (results.length === 1) {
            focusSearchResult(results[0]);
        } else {
            const bounds = L.latLngBounds([]);
            results.forEach((result) => bounds.extend(searchBounds(result)));
            map.fitBounds(bounds, { padding: [55, 55], maxZoom: 17 });
            if (isMobile()) setPanelExpanded(true);
        }
        const count = results.reduce((total, result) => total + result.segment_ids.length, 0);
        setAppStatus(`${results.length} matching street${results.length === 1 ? '' : 's'} · ${count} mapped segments`);
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
    clearDestination();
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
    clearSearch({ resetView: false });
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
    setAppStatus('Showing parking near your location.');
    if (isMobile()) setPanelExpanded(false);
});

map.on('locationerror', (event) => {
    document.getElementById('locate-btn')?.removeAttribute('aria-busy');
    const denied = event.code === 1;
    setAppStatus(denied ? 'Location access was blocked. Search for a street instead.' : 'Your location could not be found. Try a street search.', true);
    if (isMobile()) setPanelExpanded(true);
});

function setupControls() {
    document.getElementById('street-context').addEventListener('change', event => {
        if (event.target.checked) void loadStreets();
        refreshSelectionStyles();
        updateActiveLayerCount();
    });
    const input = document.getElementById('search-input');
    const clearButton = document.getElementById('clear-search-btn');
    document.getElementById('search-btn')?.addEventListener('click', () => {
        const query = input?.value.trim() || '';
        if (query) {
            if (searchMode === 'street') void searchStreets(query);
            else void searchForDestination(query);
        }
        else input?.focus();
    });
    input?.addEventListener('input', () => {
        searchRequestVersion++;
        document.getElementById('search-results').hidden = true;
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
    setupSheetToggle();
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
    setPanelExpanded(!isMobile());
    const panel = document.getElementById('app-panel');
    new ResizeObserver(() => {
        document.documentElement.style.setProperty('--sheet-height', panel.offsetHeight + 'px');
        map.invalidateSize({animate: false, debounceMoveend: true});
    }).observe(panel);
    setupParkingEvidence();
    void loadStats();
});

map.on('mouseout', resetHover);
map.on('zoomend', () => {
    refreshSelectionStyles();
    updateEvidenceRendering();
});
