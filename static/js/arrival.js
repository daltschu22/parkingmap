// Parking rules use the civil date and time at the destination, including when the device is elsewhere.
const cityClock = new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York',
    year: 'numeric', month: '2-digit', day: '2-digit', weekday: 'short',
    hour: '2-digit', minute: '2-digit', hourCycle: 'h23'});
let selectedArrival = null;
let currentMinute = -1;
let currentClock = null;

function localParkingTime(date) {
    const p = Object.fromEntries(cityClock.formatToParts(date).map(part => [part.type, part.value]));
    return {value: p.year + '-' + p.month + '-' + p.day + 'T' + p.hour + ':' + p.minute,
        minute: Number(p.hour) * 60 + Number(p.minute),
        day: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'].indexOf(p.weekday),
        month: Number(p.month), week: Math.ceil(Number(p.day) / 7)};
}

function arrivalClock() {
    if (selectedArrival) return selectedArrival;
    const minute = Math.floor(Date.now() / 60000);
    if (minute !== currentMinute) {
        currentClock = localParkingTime(new Date());
        currentMinute = minute;
    }
    return currentClock;
}

function parseArrival(value) {
    if (!/^\d{4}-\d\d-\d\dT\d\d:\d\d$/.test(value || '')) return null;
    // Boston uses these two UTC offsets. Round-trip to reject nonexistent spring-forward times.
    for (const offset of ['-05:00', '-04:00']) {
        const date = new Date(value + offset);
        if (!Number.isFinite(date.getTime())) continue;
        const clock = localParkingTime(date);
        if (clock.value === value) return {...clock, date};
    }
    return null;
}

function updateArrivalLabel() {
    const label = document.getElementById('arrival-label');
    label.textContent = selectedArrival ? new Intl.DateTimeFormat('en-US', {timeZone: 'America/New_York',
        weekday: 'short', month: 'short', day: 'numeric', year: 'numeric',
        hour: 'numeric', minute: '2-digit'}).format(selectedArrival.date) : 'Now';
}

document.addEventListener('DOMContentLoaded', () => {
    const editor = document.getElementById('arrival-editor');
    const input = document.getElementById('arrival-time');
    const form = document.getElementById('arrival-form');
    selectedArrival = parseArrival(new URLSearchParams(location.search).get('arrival'));
    updateArrivalLabel();
    let openedFromMap = false;
    editor.addEventListener('toggle', () => {
        if (!editor.open) {
            if (openedFromMap && isMobile()) setPanelExpanded(false);
            openedFromMap = false;
            return;
        }
        input.setCustomValidity('');
        input.value = arrivalClock().value;
        if (isMobile()) {
            openedFromMap = document.getElementById('app-panel').classList.contains('sheet-collapsed');
            setPanelExpanded(true);
        }
    });
    function apply(clock) {
        selectedArrival = clock;
        const url = new URL(location.href);
        if (clock) url.searchParams.set('arrival', clock.value);
        else url.searchParams.delete('arrival');
        history.replaceState(null, '', url);
        updateArrivalLabel();
        editor.open = false;
        refreshSelectionStyles();
        renderVisitorChoices();
        refreshTimedDetail?.();
        editor.querySelector('summary').focus({preventScroll: true});
    }
    input.addEventListener('input', () => input.setCustomValidity(''));
    form.addEventListener('submit', event => {
        event.preventDefault();
        const clock = parseArrival(input.value);
        if (clock) apply(clock);
        else {
            input.setCustomValidity('Choose a date and time that exists in Boston.');
            input.reportValidity();
        }
    });
    document.getElementById('arrival-now').addEventListener('click', () => apply(null));
});
