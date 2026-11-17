// Visitor choices are attached to spaces, facilities, reference intersections or addresses.
const visitorLabels = { meter: 'Meter', limited: 'Time limit', free: '3h free', lot: 'Lot / garage', sunday: 'Sunday permit exception' };
let visitorFilter = 'all';
let visitorChoices = [];
let visitorListSize = 5;
let visitorLoaded = false;
let destination = null;
let destinationPin = null;
let radiusMiles = 1;
let searchMode = 'destination';
const visitorPins = L.layerGroup().addTo(map);
const parkingColors = {meter: '#0369a1', limited: '#087454', free: '#087454', lot: '#6944a3', sunday: '#475569'};
const visitorRenderer = L.canvas({padding: 0.25, tolerance: 4});

function clearDestination() {
    if (destination) {
        const url = new URL(location.href);
        url.searchParams.delete('destination');
        history.replaceState(null, '', url);
    }
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
    const url = new URL(location.href);
    url.searchParams.set('destination', result.name + ', ' + result.city);
    history.replaceState(null, '', url);
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

function noSundayMeterFee(option, local) {
    return local.day === 0 && option.kind === 'meter' &&
        (option.city === 'Cambridge' || option.sunday_permit_exception);
}

function noMeterFee(option, local) {
    return noSundayMeterFee(option, local) || (option.kind === 'meter' && option.meter_schedule &&
        !option.meter_schedule.some(period => period.days.includes(local.day) && inWindow(local.minute, period.window)));
}

function visitorMatches(option, local) {
    return visitorFilter === 'all' || option.kind === visitorFilter ||
        (visitorFilter === 'lot' && Boolean(option.facility)) || (visitorFilter === 'free' && noMeterFee(option, local));
}

function parkingLocationName(option) {
    return option.anchor_type === 'address' ? 'Near ' + option.anchor : option.name;
}

function parkingLocationNote(option) {
    if (option.kind === 'sunday' && option.anchor_type === 'address') return 'The Sunday exception applies in front of the listed address. Follow signs for other curb restrictions.';
    if (option.anchor_type === 'address') return 'The pin marks the address. The city gives no start or end for this zone. Use the posted signs to identify the spaces it covers.';
    if (option.anchor_type === 'street') return 'This point locates the street. The Sunday exception covers its resident-permit requirement. Use the signs to identify a legal space.';
    if (option.section_traces) return 'The line shows the approximate signed stretch on the listed side. Check its boundaries on the posted signs.';
    if (option.anchor) return 'The pin marks the reference intersection. Follow the listed curb description and signs to locate the spaces.';
    return option.facility ? 'Use the signed public spaces in this facility.' : 'The marker identifies a mapped meter space. Check its posted terms.';
}

function openParkingOption(option, recenter = true) {
    ++detailRequestVersion;
    ++searchRequestVersion;
    searchResultStreetKeys.clear();
    selectStreet(null);
    document.getElementById('search-results').hidden = true;
    setAppStatus('');
    if (recenter) map.setView(mapPoint(option), Math.max(map.getZoom(), 17), { animate: false });
    const parkingPoint = encodeURIComponent(option.point.join(','));
    const walkTo = destination ? encodeURIComponent(destination.point.join(',')) : '';
    const reference = option.anchor_type || 'intersection';
    const local = arrivalClock();
    const source = safeExternalUrl(option.source);
    const rate = option.rate && !noMeterFee(option, local) ? formatMeterRate(option.rate) : '';
    document.getElementById('street-details').innerHTML = `
        <article class="parking-answer">
            <p class="answer-eyebrow">${escapeHtml(mapRule(option, local))} · ${escapeHtml(option.city)}</p>
            <h3 class="answer-title">${escapeHtml(parkingLocationName(option))}</h3>
            <p class="answer-location">${escapeHtml(option.where)}</p>
            ${sundayNote(option, local) ? `<p class="answer-summary">${escapeHtml(sundayNote(option, local))}</p>` : ''}
            <p class="answer-summary">${sundayNote(option, local) && (local.day === 0 || noMeterFee(option, local)) && option.kind !== 'sunday' ? 'Regular schedule · ' : ''}${escapeHtml([rate, option.rule].filter(Boolean).join(' · '))}</p>
            ${option.access ? `<p class="answer-summary">${escapeHtml(option.access)}</p>` : ''}
            ${option.restrictions ? `<p class="answer-summary">${escapeHtml(option.restrictions)}</p>` : ''}
            <p class="answer-freshness">${escapeHtml(parkingLocationNote(option))}</p>
            <div class="answer-actions">
                <a class="answer-action answer-action--primary" href="https://www.google.com/maps/dir/?api=1&amp;destination=${parkingPoint}" target="_blank" rel="noopener noreferrer">${option.anchor ? `Directions to ${reference}` : 'Directions to parking'}</a>
                ${walkTo ? `<a class="answer-action" href="https://www.google.com/maps/dir/?api=1&amp;travelmode=walking&amp;origin=${parkingPoint}&amp;destination=${walkTo}" target="_blank" rel="noopener noreferrer">Walk to destination</a>` : ''}
                ${source ? `<a class="answer-action" href="${escapeHtml(source)}" target="_blank" rel="noopener noreferrer">${escapeHtml(option.citation)}</a>` : ''}
            </div>
        </article>`;
    showSelectionPanel(false, recenter);
    refreshTimedDetail = () => {
        if (visitorEligible(option, arrivalClock())) openParkingOption(option, false);
        else {
            clearStreetSelection();
            setAppStatus(option.name + ' falls outside the documented visitor options for this arrival.');
        }
    };
}

function mapPoint(option) {
    // Place the label on the matched side. Driving directions still use the published reference.
    const line = option.section_traces?.[0];
    if (!line) return option.point;
    const first = line[Math.floor((line.length - 1) / 2)];
    const next = line[Math.floor(line.length / 2)];
    return [(first[0] + next[0]) / 2, (first[1] + next[1]) / 2];
}

function sundayNote(option, local) {
    if (noSundayMeterFee(option, local)) return 'No meter payment on Sunday. Check the other posted curb rules.';
    if (noMeterFee(option, local)) return 'Arrival is outside the published meter payment hours. Other curb rules still apply.';
    if (option.kind === 'sunday' || (local.day === 0 && option.sunday_permit_exception)) {
        return 'Sunday resident-permit enforcement is suspended here. Other posted limits still apply.';
    }
    if (option.sunday_permit_exception) return 'Somerville suspends resident-permit enforcement on Sundays.';
    return '';
}

function mapRule(option, local) {
    if (noSundayMeterFee(option, local)) return 'No Sunday meter fee';
    if (noMeterFee(option, local)) return 'Outside meter hours';
    if (option.kind === 'sunday') return 'Sunday permit exception';
    if (local.day === 0 && option.sunday_permit_exception && option.visitor_window &&
        !inWindow(local.minute, option.visitor_window)) return 'Sunday permit exception';
    if (option.kind === 'meter' && option.rate) return 'Meter · ' + formatMeterRate(option.rate).replace('/hour', '/hr');
    if (option.kind === 'free') return 'First 3 hours free';
    if (option.kind === 'lot') return /metered/i.test(option.rule) ? 'Metered lot' : 'Public lot / garage';
    return visitorLabel(option);
}

function inWindow(minute, [start, end]) {
    return start < end ? minute >= start && minute < end : minute >= start || minute < end;
}

function visitorEligible(option, local) {
    if (/customer parking|park facility visitor parking only/i.test([option.access, option.rule].join(' '))) return false;
    if (option.kind === 'sunday') return local.day === 0;
    if (option.visitor_days && !option.visitor_days.includes(local.day)) return false;
    const sundayExemption = local.day === 0 && option.sunday_permit_exception;
    if (!sundayExemption && option.permit_window && inWindow(local.minute, option.permit_window)) return false;
    if (option.anchor && option.visitor_window && !inWindow(local.minute, option.visitor_window) &&
        !(sundayExemption && (option.kind === 'meter' || /permit|rpp/i.test(option.rule)))) return false;
    const closed = option.closure;
    return !(closed && local.day === closed.day && inWindow(local.minute, closed.window) &&
        (!closed.months.length || local.month >= closed.months[0] && local.month <= closed.months[1]) &&
        (!closed.weeks.length || closed.weeks.includes(local.week)));
}

function shortTime(minutes) {
    const hour = Math.floor(minutes / 60);
    const remainder = minutes % 60;
    return (hour % 12 || 12) + (remainder ? ':' + String(remainder).padStart(2, '0') : '') + (hour < 12 ? 'am' : 'pm');
}

function drawVisitorMap(inView, local) {
    visitorPins.clearLayers();
    const placed = [];
    const named = new Set();
    const size = map.getSize();
    const origin = map.getContainer().getBoundingClientRect();
    const controls = [...document.querySelectorAll('.parking-filter-bar, .leaflet-control-zoom, .map-evidence-key, .map-panel-button')]
        .map(el => el.getBoundingClientRect()).filter(rect => rect.width && rect.height)
        .map(rect => ({x: rect.left - origin.left, y: rect.top - origin.top, w: rect.width, h: rect.height}));
    const detail = map.getZoom() >= 16;
    for (const option of inView) {
        const color = parkingColors[option.kind];
        const point = mapPoint(option);
        const select = () => openParkingOption(option);
        for (const trace of option.section_traces || []) {
            L.polyline(trace, {renderer: visitorRenderer, color, weight: detail ? 6 : 4,
                opacity: 0.95, lineCap: 'butt'}).on('click', select).addTo(visitorPins);
        }
        const reference = Boolean(option.anchor && !option.section_traces);
        if (!option.section_traces || !detail) {
            L.circleMarker(point, {renderer: visitorRenderer, radius: detail ? 6 : 4,
                color: reference ? color : '#fff', weight: reference ? 3 : 1.5,
                fillColor: reference ? '#fff' : color, fillOpacity: 1
            }).on('click', select).addTo(visitorPins);
        }
    }
    // Give different street names space first. Keep a point or trace for every result.
    const firstPerName = inView.filter(option => {
        const key = option.city + '|' + option.name;
        if (named.has(key)) return false;
        named.add(key);
        return true;
    });
    const labelWidth = 142, labelHeight = 49;
    for (const option of new Set([...firstPerName, ...inView])) {
        const point = mapPoint(option);
        const p = map.latLngToContainerPoint(point);
        const left = p.x - labelWidth / 2;
        const top = [p.y - labelHeight - 6, p.y + 6].find(y =>
            left >= 0 && left + labelWidth <= size.x && y >= 0 && y + labelHeight <= size.y &&
            ![...controls, ...placed].some(box => left < box.x + box.w + 2 && left + labelWidth + 2 > box.x &&
                y < box.y + box.h + 2 && y + labelHeight + 2 > box.y));
        if (top === undefined) continue;
        placed.push({x: left, y: top, w: labelWidth, h: labelHeight});
        const side = option.where.match(/^(North|South|West|East|NW|NE|SW|SE|Both) side/i)?.[1];
        const evidence = option.section_traces ? (side === 'Both' ? 'both sides' : side + ' side') :
            (option.anchor ? 'near ' + option.anchor : '');
        const label = mapRule(option, local);
        const extra = label === 'Sunday permit exception' || noSundayMeterFee(option, local) ? evidence :
            option.visitor_window?.map(shortTime).join('–') || evidence;
        const streetName = parkingLocationName(option).replace(/^Massachusetts Ave(nue)?$/i, 'Mass Ave');
        L.marker(point, {
            icon: L.divIcon({
                className: 'visitor-pin ' + option.kind + (top > p.y ? ' pin-below' : ''),
                html: `<span class="pin-card"><strong>${escapeHtml(streetName)}</strong>
                    <span>${escapeHtml(label)}</span>${extra ? `<small>${escapeHtml(extra)}</small>` : ''}</span>`,
                iconSize: [labelWidth, labelHeight], iconAnchor: [labelWidth / 2, p.y - top]
            }),
            title: [option.name, label, option.where, option.rule].join(' · '),
            alt: option.name + ' · ' + label,
            zIndexOffset: 150
        }).on('click', () => openParkingOption(option)).addTo(visitorPins);
    }
}

function renderVisitorChoices() {
    if (!visitorLoaded) return;
    const bounds = map.getBounds();
    const center = destination ? L.latLng(destination.point) : selectedStreetLayer?.getBounds().getCenter() || map.getCenter();
    const local = arrivalClock();
    const options = visitorChoices.filter(option => visitorMatches(option, local))
        .filter(option => visitorEligible(option, local))
        .map(option => ({option, distance: center.distanceTo(mapPoint(option))}))
        .sort((a, b) => a.distance - b.distance);
    const nearby = options.filter(item => !destination || item.distance <= radiusMiles * 1609.344).map(item => item.option);
    const inView = nearby.filter(option => bounds.contains(mapPoint(option)) ||
        option.section_traces?.some(line => bounds.intersects(L.latLngBounds(line))));
    const rows = destination ? nearby : inView;
    drawVisitorMap(inView, local);
    const nearest = !destination && !inView.length && nearby[0];
    document.getElementById('map-no-options').hidden = inView.length > 0;
    for (const id of ['nearest-parking', 'map-nearest-parking']) {
        const button = document.getElementById(id);
        button.hidden = !nearest;
        button.onclick = nearest ? () => {
            map.setView(mapPoint(nearest), 17);
            if (isMobile()) setPanelExpanded(false);
        } : null;
    }
    const status = document.getElementById('parking-list-status');
    status.textContent = inView.length ? (selectedStreetLayer ? 'Closest to this block · posted hours apply' : 'Closest to map center · for your arrival') :
        'No documented visitor options in this view. Zoom out or search another destination.';
    if (destination) {
        status.textContent = rows.length ? `Nearest options within ${radiusMiles} mi · straight-line distance for your arrival` :
            `No documented ${visitorFilter === 'all' ? 'visitor parking' : visitorLabels[visitorFilter]} options within ${radiusMiles} mi of this destination.`;
    }
    document.getElementById('expand-distance').hidden = !destination || radiusMiles >= 3 || rows.length >= visitorListSize;
    if (local.day === 0 && (destination?.city === 'Somerville' || inView.some(o => o.sunday_permit_exception))) {
        status.textContent += ' · Somerville suspends resident-permit enforcement on Sundays.';
    }
    if (visitorFilter === 'free' && rows.length) {
        status.textContent += ' · Free garage periods and meters outside payment hours';
    }
    const list = document.getElementById('parking-options');
    list.replaceChildren();
    for (const option of rows.slice(0, visitorListSize)) {
        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'parking-option';
        const miles = center.distanceTo(mapPoint(option)) / 1609.344;
        const distance = miles < 0.1 ? 'Under 0.1 mi' : miles.toFixed(1) + ' mi';
        const rule = (local.day === 0 || noMeterFee(option, local)) && sundayNote(option, local) ? sundayNote(option, local) : option.kind === 'free' ? 'First 3 hours free · daily reset at 5 a.m.' :
            [option.rate && !noMeterFee(option, local) ? formatMeterRate(option.rate) : '', option.rule].filter(Boolean).join(' · ');
        button.innerHTML = `<span class="option-line"><strong>${escapeHtml(parkingLocationName(option))}</strong><span class="visitor-tag ${option.kind}">${escapeHtml(mapRule(option, local))}</span></span>
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
            if (searchMode === 'street') void loadStreets();
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
        // Update at time boundaries without replacing the focused result every minute.
        function eligibleFlags() {
            const local = arrivalClock();
            return local.day + ':' + visitorChoices.map(o => visitorEligible(o, local) ? (noMeterFee(o, local) ? '2' : '1') : '0').join('');
        }
        let lastEligible = eligibleFlags();
        function refreshHours() {
            const next = eligibleFlags();
            if (next !== lastEligible) {
                lastEligible = next;
                refreshSelectionStyles();
                renderVisitorChoices();
                refreshTimedDetail?.();
            }
        }
        window.setInterval(refreshHours, 30000);
        document.addEventListener('visibilitychange', () => { if (!document.hidden) refreshHours(); });
    } catch (error) {
        document.getElementById('parking-list-status').textContent = 'Visitor parking could not be loaded. Reload to retry.';
    }
});
