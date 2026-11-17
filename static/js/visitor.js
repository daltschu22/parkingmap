// Visitor choices are attached to individual spaces, facilities or reference intersections.
const visitorLabels = { meter: 'Meter', limited: 'Time limit', free: '3h free', lot: 'Lot / garage' };
let visitorFilter = 'all';
let visitorChoices = [];
let visitorListSize = 5;
let visitorLoaded = false;
const visitorPins = L.layerGroup().addTo(map);

function visitorLabel(option) {
    if (option.kind === 'meter' && option.anchor) return 'Meter zone';
    const time = option.kind === 'limited' && option.rule.match(/^(\d+|½)\s*(hour|hr|h|min)/i);
    if (time) return time[1] + (time[2].toLowerCase() === 'min' ? ' min' : 'h') + ' limit';
    return visitorLabels[option.kind];
}

function visitorLayerVisible(key) {
    if (visitorFilter === 'all') return true;
    if (key === 'meters') return visitorFilter === 'meter';
    // Free results have their own labeled pins. A generic facility layer mixes tariffs.
    return key === 'public-facilities' && visitorFilter === 'lot';
}

function visitorMatches(option) {
    return visitorFilter === 'all' || option.kind === visitorFilter ||
        (visitorFilter === 'lot' && Boolean(option.facility));
}

function openParkingOption(option) {
    ++detailRequestVersion;
    ++searchRequestVersion;
    searchResultStreetKeys.clear();
    selectStreet(null);
    document.getElementById('search-results').hidden = true;
    setAppStatus('');
    map.setView(option.point, Math.max(map.getZoom(), 17), { animate: false });
    const destination = encodeURIComponent(option.point.join(','));
    const source = safeExternalUrl(option.source);
    const qualifier = option.anchor ? 'Reference intersection · follow the signed side and limits' :
        (option.facility ? 'Public-access facility' : 'Mapped meter space');
    const rate = option.rate ? formatMeterRate(option.rate) : '';
    document.getElementById('street-details').innerHTML = `
        <article class="parking-answer">
            <p class="answer-eyebrow">${visitorLabel(option)} · ${escapeHtml(option.city)}</p>
            <h3 class="answer-title">${escapeHtml(option.name)}</h3>
            <p class="answer-location">${escapeHtml(option.where)}</p>
            <p class="answer-summary">${escapeHtml([rate, option.rule].filter(Boolean).join(' · '))}</p>
            ${option.access ? `<p class="answer-summary">${escapeHtml(option.access)}</p>` : ''}
            ${option.restrictions ? `<p class="answer-summary">${escapeHtml(option.restrictions)}</p>` : ''}
            <p class="answer-freshness">${qualifier}. Check signs on arrival. Occupancy is not live.</p>
            <div class="answer-actions">
                <a class="answer-action answer-action--primary" href="https://www.google.com/maps/dir/?api=1&amp;destination=${destination}" target="_blank" rel="noopener noreferrer">${option.anchor ? 'Directions to intersection' : 'Directions'}</a>
                ${source ? `<a class="answer-action" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${escapeHtml(option.citation)}</a>` : ''}
            </div>
        </article>`;
    showSelectionPanel();
}

function renderVisitorChoices() {
    if (!visitorLoaded) return;
    const bounds = map.getBounds();
    const center = selectedStreetLayer?.getBounds().getCenter() || map.getCenter();
    const options = visitorChoices.filter(visitorMatches)
        .map(option => ({ option, distance: center.distanceTo(option.point) }))
        .sort((a, b) => a.distance - b.distance).map(item => item.option);
    const inView = options.filter(option => bounds.contains(option.point));
    const rows = inView.length ? inView : options;
    visitorPins.clearLayers();
    const placed = [];
    // Reserve one label for each available kind before filling the remaining map.
    const representatives = [...new Set(inView.map(option => option.kind))]
        .map(kind => inView.find(option => option.kind === kind));
    for (const option of [...new Set([...representatives, ...inView])]) {
        const p = map.latLngToContainerPoint(option.point);
        if (placed.some(q => Math.abs(p.x - q.x) < 128 && Math.abs(p.y - q.y) < 48)) continue;
        placed.push(p);
        const label = visitorLabel(option);
        L.marker(option.point, {
            icon: L.divIcon({
                className: 'visitor-pin',
                html: `<span class="visitor-tag ${option.kind}">${label}<small>${escapeHtml(option.name)}</small></span>`,
                iconSize: [120, 38],
                iconAnchor: [60, 19]
            }),
            title: `${label} · ${option.name} · ${option.where}`,
            alt: `${label} · ${option.name}`,
            zIndexOffset: 150
        }).on('click', () => openParkingOption(option)).addTo(visitorPins);
    }
    const status = document.getElementById('parking-list-status');
    status.textContent = inView.length ? (selectedStreetLayer ? 'Closest to this block · posted hours apply' : 'Closest to the map center · posted hours apply') :
        (rows.length ? 'No matches in view. Nearest mapped options:' : 'No documented options in this category.');
    if (visitorFilter === 'free' && rows.length) {
        status.textContent += ' · Verified free periods at garages only';
    }
    const list = document.getElementById('parking-options');
    list.replaceChildren();
    for (const option of rows.slice(0, visitorListSize)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'parking-option';
        const miles = center.distanceTo(option.point) / 1609.344;
        const distance = miles < 0.1 ? 'Under 0.1 mi' : miles.toFixed(1) + ' mi';
        const rule = option.kind === 'free' ? 'First 3 hours free · daily reset at 5 a.m.' :
            [option.rate ? formatMeterRate(option.rate) : '', option.rule].filter(Boolean).join(' · ');
        button.innerHTML = `<span class="option-line"><span class="visitor-tag ${option.kind}">${visitorLabel(option)}</span><strong>${escapeHtml(option.name)}</strong></span>
            <span class="option-place">${distance} · ${escapeHtml(option.city)} · ${escapeHtml(option.where)}</span>
            <span class="option-rule">${escapeHtml(rule)}</span>`;
        button.addEventListener('click', () => openParkingOption(option));
        list.append(button);
    }
    document.getElementById('more-parking').hidden = rows.length <= visitorListSize;
}

document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('[data-parking-filter]').forEach(button => {
        button.addEventListener('click', () => {
            visitorFilter = button.dataset.parkingFilter;
            visitorListSize = 5;
            document.querySelectorAll('[data-parking-filter]').forEach(item => {
                item.setAttribute('aria-pressed', String(item === button));
            });
            clearSearch({ resetView: false });
            if (isMobile()) setPanelExpanded(true);
            renderVisitorChoices();
            updateEvidenceRendering();
        });
    });
    document.getElementById('more-parking').addEventListener('click', () => {
        visitorListSize += 10;
        renderVisitorChoices();
    });
    try {
        visitorChoices = await fetchJson('/api/visitor-parking');
        visitorLoaded = true;
        renderVisitorChoices();
        map.on('moveend resize', renderVisitorChoices);
    } catch (error) {
        document.getElementById('parking-list-status').textContent = 'Visitor parking could not be loaded. Reload to retry.';
    }
});
