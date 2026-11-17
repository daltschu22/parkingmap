// Visitor choices are attached to spaces, facilities, reference intersections or addresses.
const visitorLabels = { meter: 'Meter', limited: 'Time limit', free: '3h free', lot: 'Lot / garage' };
let visitorFilter = 'all';
let visitorChoices = [];
let visitorListSize = 5;
let visitorLoaded = false;
let destination = null;
let destinationPin = null;
let radiusMiles = 1;
let searchMode = 'destination';
const visitorPins = L.layerGroup().addTo(map);

function clearDestination() {
    destination = null;
    radiusMiles = 1;
    if (destinationPin) destinationPin.remove();
    destinationPin = null;
    document.getElementById('destination-context').hidden = true;
    updateEvidenceRendering();
    visitorListSize = 5;
    renderVisitorChoices();
}

function focusDestination(result) {
    clearStreetSelection();
    clearDestination();
    destination = result;
    document.getElementById('search-results').hidden = true;
    document.getElementById('search-input').value = result.name + ', ' + result.city;
    document.getElementById('clear-search-btn').hidden = false;
    document.getElementById('destination-context').hidden = false;
    document.getElementById('center-destination').textContent = 'Destination · ' + result.name + ', ' + result.city;
    setAppStatus('');
    destinationPin = L.marker(result.point, {
        title: 'Destination: ' + result.name,
        alt: 'Destination: ' + result.name,
        zIndexOffset: 1000,
        icon: L.divIcon({className: 'destination-marker', html: '<span aria-hidden="true">◎</span>',
            iconSize: [28, 28], iconAnchor: [14, 14]})
    }).addTo(map);
    updateEvidenceRendering();
    map.setView(result.point, 16, {animate: false});
    if (isMobile()) setPanelExpanded(true);
    renderVisitorChoices();
}

async function searchForDestination(query) {
    const version = ++searchRequestVersion;
    clearDestination();
    searchResultStreetKeys.clear();
    clearStreetSelection();
    document.getElementById('empty-state').hidden = true;
    if (isMobile()) setPanelExpanded(true);
    const box = document.getElementById('search-results');
    box.hidden = true;
    if (query.length < 2) {
        setAppStatus('Enter an address or a place name.', true);
        return;
    }
    setAppStatus('Finding your destination…');
    try {
        const { results } = await fetchJson('/api/destinations?q=' + encodeURIComponent(query));
        if (version !== searchRequestVersion) return;
        if (!results.length) {
            if (!/^\d/.test(query)) return searchStreets(query, true);
            setAppStatus('Address not found. Try the street number, street, and city in Cambridge, Medford, or Somerville.', true);
            return;
        }
        if (results.length === 1) {
            focusDestination(results[0]);
            return;
        }
        box.replaceChildren();
        for (const result of results) {
            const button = document.createElement('button');
            button.type = 'button';
            button.className = 'search-result-button';
            button.innerHTML = `<span><strong class="search-result-name">${escapeHtml(result.name)}</strong>
                <span class="search-result-city">${escapeHtml([result.address, result.city].filter(Boolean).join(' · '))}</span></span>
                <span class="search-result-count">${escapeHtml(result.kind)}</span>`;
            button.addEventListener('click', () => focusDestination(result));
            box.append(button);
        }
        box.hidden = false;
        setAppStatus('Choose your destination to see parking nearby.');
    } catch (error) {
        if (version === searchRequestVersion) setAppStatus('Destination search could not load. Please try again.', true);
    }
}

function visitorLabel(option) {
    if (option.kind === 'meter' && option.anchor) return 'Meter zone';
    const time = option.kind === 'limited' && option.rule.match(/^(\d+|½)\s*(hour|hr|h|min)/i);
    if (time) return time[1] + (time[2].toLowerCase() === 'min' ? ' min' : 'h') + ' limit';
    return visitorLabels[option.kind];
}

function visitorLayerVisible(key) {
    if (destination && key === 'public-facilities') return false;
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
    const parkingPoint = encodeURIComponent(option.point.join(','));
    const walkTo = destination ? encodeURIComponent(destination.point.join(',')) : '';
    const reference = option.anchor_type === 'address' ? 'address' : 'intersection';
    const source = safeExternalUrl(option.source);
    const qualifier = option.anchor ? `Reference ${reference} · check the marked curb and posted limits` :
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
                <a class="answer-action answer-action--primary" href="https://www.google.com/maps/dir/?api=1&amp;destination=${parkingPoint}" target="_blank" rel="noopener noreferrer">${option.anchor ? `Directions to ${reference}` : 'Directions to parking'}</a>
                ${walkTo ? `<a class="answer-action" href="https://www.google.com/maps/dir/?api=1&amp;travelmode=walking&amp;origin=${parkingPoint}&amp;destination=${walkTo}" target="_blank" rel="noopener noreferrer">Walk to destination</a>` : ''}
                ${source ? `<a class="answer-action" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${escapeHtml(option.citation)}</a>` : ''}
            </div>
        </article>`;
    showSelectionPanel();
}

function renderVisitorChoices() {
    if (!visitorLoaded) return;
    const bounds = map.getBounds();
    const center = destination ? L.latLng(destination.point) : selectedStreetLayer?.getBounds().getCenter() || map.getCenter();
    const options = visitorChoices.filter(visitorMatches)
        .map(option => ({ option, distance: center.distanceTo(option.point) }))
        .sort((a, b) => a.distance - b.distance).map(item => item.option);
    const nearby = destination ? options.filter(option =>
        center.distanceTo(option.point) <= radiusMiles * 1609.344 &&
        !/customer parking|park facility visitor parking only/i.test([option.access, option.rule].join(' '))) : options;
    const inView = nearby.filter(option => bounds.contains(option.point));
    const rows = destination ? nearby : (inView.length ? inView : options);
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
    if (destination) {
        status.textContent = rows.length ? `Nearest options within ${radiusMiles} mi · straight-line distance · check posted hours` :
            `No documented ${visitorFilter === 'all' ? 'visitor parking' : visitorLabels[visitorFilter]} options within ${radiusMiles} mi of this destination.`;
    }
    document.getElementById('expand-distance').hidden = !destination || radiusMiles >= 3 || rows.length >= visitorListSize;
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
            <span class="option-rule">${escapeHtml(rule)}</span>
            ${destination && option.restrictions ? `<span class="option-rule">${escapeHtml(option.restrictions)}</span>` : ''}`;
        button.addEventListener('click', () => openParkingOption(option));
        list.append(button);
    }
    document.getElementById('more-parking').hidden = rows.length <= visitorListSize;
}

document.addEventListener('DOMContentLoaded', async () => {
    document.querySelectorAll('[data-search-mode]').forEach(button => {
        button.addEventListener('click', () => {
            if (button.dataset.searchMode === searchMode) return;
            searchMode = button.dataset.searchMode;
            clearSearch({resetView: false});
            document.querySelectorAll('[data-search-mode]').forEach(item =>
                item.setAttribute('aria-pressed', String(item === button)));
            const input = document.getElementById('search-input');
            input.placeholder = searchMode === 'street' ? 'Street + city' : 'Where are you going?';
            document.getElementById('search-label').textContent = searchMode === 'street' ? 'Street and city' : 'Destination address or place name';
            input.focus();
        });
    });
    document.getElementById('center-destination').addEventListener('click', () => {
        if (!destination) return;
        clearStreetSelection();
        map.setView(destination.point, 16);
        renderVisitorChoices();
    });
    document.getElementById('expand-distance').addEventListener('click', () => {
        radiusMiles = 3;
        renderVisitorChoices();
    });
    document.querySelectorAll('[data-parking-filter]').forEach(button => {
        button.addEventListener('click', () => {
            visitorFilter = button.dataset.parkingFilter;
            visitorListSize = 5;
            document.querySelectorAll('[data-parking-filter]').forEach(item => {
                item.setAttribute('aria-pressed', String(item === button));
            });
            if (destination) {
                clearStreetSelection();
            } else {
                clearSearch({ resetView: false });
            }
            if (isMobile()) setPanelExpanded(true);
            renderVisitorChoices();
            updateEvidenceRendering();
        });
    });
    document.getElementById('more-parking').addEventListener('click', () => {
        visitorListSize += 10;
        renderVisitorChoices();
    });
    const requested = new URLSearchParams(window.location.search).get('destination')?.trim();
    if (requested && requested.length <= document.getElementById('search-input').maxLength) {
        document.getElementById('search-input').value = requested;
        document.getElementById('clear-search-btn').hidden = false;
        void searchForDestination(requested);
    }
    try {
        visitorChoices = await fetchJson('/api/visitor-parking');
        visitorLoaded = true;
        renderVisitorChoices();
        map.on('moveend resize', renderVisitorChoices);
    } catch (error) {
        document.getElementById('parking-list-status').textContent = 'Visitor parking could not be loaded. Reload to retry.';
    }
});
