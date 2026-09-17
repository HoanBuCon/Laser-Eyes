/**
 * VIGIL AI — Interactive Seat ROI Calibration Tool JavaScript
 *
 * Implements:
 * - Room and Camera feed selector
 * - Reference frame grabbing from RTSP or file upload
 * - Interactive multi-point polygon drawing (N >= 4 points)
 * - Undo point, clear polygon, delete seat, enable/disable seat
 * - Resolution scaling & normalization protection
 * - Direct CSDL persistence via /api/v1/rooms/{room_id}/seats/bulk
 */

let rooms = [];
let cameras = [];
let currentRoomId = '';
let currentCameraId = '';

let referenceImage = null;
let refWidth = 1280;
let refHeight = 720;

let seats = [];
let currentPolygon = [];
let mousePos = { x: 0, y: 0 };
let selectedSeatIndex = null;
let hoveredSeatIndex = null;
let isDrawing = false;
let editorMode = 'SELECT'; // 'SELECT' (default cursor/select) or 'DRAW' (polygon vertex pinning)

const canvas = document.getElementById('calibrationCanvas');
const ctx = canvas.getContext('2d');

document.addEventListener('DOMContentLoaded', () => {
    initCanvasEvents();
    loadRooms();
    window.addEventListener('keydown', handleGlobalKeydown);
    setEditorMode('SELECT');
});

// ==============================================================================
// 1. Room & Camera Loading
// ==============================================================================
async function loadRooms() {
    try {
        const res = await fetch('/api/v1/rooms');
        if (!res.ok) return;
        rooms = await res.json();

        const selectRoom = document.getElementById('selectRoom');
        if (rooms.length === 0) {
            selectRoom.innerHTML = `<option value="">No rooms configured</option>`;
            return;
        }

        selectRoom.innerHTML = rooms.map(r => `
            <option value="${r.id}">${r.room_code ? `[${r.room_code}] ` : ''}${r.name}</option>
        `).join('');

        currentRoomId = rooms[0].id;
        loadCamerasForRoom(currentRoomId);
    } catch (err) {
        console.error('Failed to load rooms:', err);
    }
}

async function loadCamerasForRoom(roomId) {
    try {
        const res = await fetch(`/api/v1/rooms/${roomId}/cameras`);
        const selectCam = document.getElementById('selectCamera');

        if (res.ok) {
            cameras = await res.json();
        } else {
            cameras = [];
        }

        if (cameras.length === 0) {
            // Fallback: list all cameras or allow default
            const allCamRes = await fetch('/api/v1/cameras');
            if (allCamRes.ok) cameras = await allCamRes.json();
        }

        if (cameras.length > 0) {
            selectCam.innerHTML = cameras.map(c => `
                <option value="${c.id}">${c.name} (${c.position || 'Overhead'})</option>
            `).join('');
            currentCameraId = cameras[0].id;
        } else {
            selectCam.innerHTML = `<option value="cam-default">Default Camera</option>`;
            currentCameraId = 'cam-default';
        }

        // Auto-load seats and reference frame for this room/camera
        reloadSeatsFromDB();
        fetchCameraReferenceFrame();
    } catch (err) {
        console.error('Failed to load cameras for room:', err);
    }
}

function onRoomChanged() {
    currentRoomId = document.getElementById('selectRoom').value;
    loadCamerasForRoom(currentRoomId);
}

function onCameraChanged() {
    currentCameraId = document.getElementById('selectCamera').value;
    reloadSeatsFromDB();
    fetchCameraReferenceFrame();
}

// ==============================================================================
// 2. Reference Frame Acquisition (RTSP Grab / Upload)
// ==============================================================================
async function fetchCameraReferenceFrame() {
    setDrawingStatus('LOADING FRAME...', 'text-amber-400');
    try {
        const url = `/api/v1/cameras/${currentCameraId}/reference-frame?t=${Date.now()}`;
        const img = new Image();
        img.crossOrigin = "anonymous";
        img.onload = () => {
            referenceImage = img;
            refWidth = img.naturalWidth || 1280;
            refHeight = img.naturalHeight || 720;
            canvas.width = refWidth;
            canvas.height = refHeight;

            document.getElementById('canvasResLabel').innerText = `${refWidth} × ${refHeight}`;
            document.getElementById('canvasEmptyPlaceholder').classList.add('hidden');
            setDrawingStatus('READY TO DRAW', 'text-emerald-400');
            redrawCanvas();
        };
        img.onerror = () => {
            setDrawingStatus('OFFLINE (FALLBACK DEMO)', 'text-amber-400');
            // Try demo fallback directly
            loadFallbackDemoFrame();
        };
        img.src = url;
    } catch (e) {
        console.warn('Reference frame grab error:', e);
        loadFallbackDemoFrame();
    }
}

function loadFallbackDemoFrame() {
    const img = new Image();
    img.onload = () => {
        referenceImage = img;
        refWidth = img.naturalWidth || 1280;
        refHeight = img.naturalHeight || 720;
        canvas.width = refWidth;
        canvas.height = refHeight;
        document.getElementById('canvasResLabel').innerText = `${refWidth} × ${refHeight}`;
        document.getElementById('canvasEmptyPlaceholder').classList.add('hidden');
        setDrawingStatus('DEMO FRAME LOADED', 'text-cyan-400');
        redrawCanvas();
    };
    img.src = '/static/img/demo_classroom_frame.jpg';
}

async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    setDrawingStatus('UPLOADING FILE...', 'text-amber-400');
    const formData = new FormData();
    formData.append('file', file);

    try {
        const res = await fetch('/api/v1/cameras/reference-frame/upload', {
            method: 'POST',
            body: formData
        });
        if (!res.ok) throw new Error('Upload failed');
        const data = await res.json();

        const img = new Image();
        img.onload = () => {
            referenceImage = img;
            refWidth = data.width || img.naturalWidth || 1280;
            refHeight = data.height || img.naturalHeight || 720;
            canvas.width = refWidth;
            canvas.height = refHeight;
            document.getElementById('canvasResLabel').innerText = `${refWidth} × ${refHeight}`;
            document.getElementById('canvasEmptyPlaceholder').classList.add('hidden');
            setDrawingStatus('UPLOADED FRAME READY', 'text-emerald-400');
            redrawCanvas();
        };
        img.src = data.image_url;
    } catch (err) {
        alert('Failed to upload and extract frame: ' + err.message);
        setDrawingStatus('ERROR', 'text-red-400');
    }
}

// ==============================================================================
// 3. Canvas Mouse Events, Editor Modes & Interactive Drawing
// ==============================================================================
function initCanvasEvents() {
    canvas.addEventListener('mousedown', handleCanvasMouseDown);
    canvas.addEventListener('mousemove', handleCanvasMouseMove);
    canvas.addEventListener('dblclick', handleCanvasDblClick);
}

function setEditorMode(mode) {
    editorMode = mode;
    const btnSelect = document.getElementById('btnModeSelect');
    const btnDraw = document.getElementById('btnModeDraw');

    if (btnSelect && btnDraw) {
        if (mode === 'SELECT') {
            btnSelect.className = 'px-2.5 py-1 rounded-md text-xs font-semibold transition flex items-center gap-1.5 bg-cyan-600 text-white shadow';
            btnDraw.className = 'px-2.5 py-1 rounded-md text-xs font-semibold text-gray-400 hover:text-white transition flex items-center gap-1.5';
            canvas.style.cursor = hoveredSeatIndex !== null ? 'pointer' : 'default';
            if (currentPolygon.length > 0) {
                currentPolygon = [];
                isDrawing = false;
            }
            if (selectedSeatIndex !== null && selectedSeatIndex >= 0 && selectedSeatIndex < seats.length) {
                setDrawingStatus(`SELECTED ${seats[selectedSeatIndex].seat_code} (PRESS DEL TO DELETE)`, 'text-cyan-400');
            } else {
                setDrawingStatus('SELECT MODE (CLICK SEAT)', 'text-cyan-400');
            }
        } else { // DRAW
            btnDraw.className = 'px-2.5 py-1 rounded-md text-xs font-semibold transition flex items-center gap-1.5 bg-amber-600 text-white shadow';
            btnSelect.className = 'px-2.5 py-1 rounded-md text-xs font-semibold text-gray-400 hover:text-white transition flex items-center gap-1.5';
            canvas.style.cursor = 'crosshair';
            selectedSeatIndex = null;
            hoveredSeatIndex = null;
            updateDeleteSelectedButton();
            renderSeatList();
            setDrawingStatus('DRAW MODE (CLICK TO PIN POINTS)', 'text-amber-400');
        }
    }
    redrawCanvas();
}

function updateDeleteSelectedButton() {
    const btn = document.getElementById('btnDeleteSelectedSeat');
    if (!btn) return;
    if (selectedSeatIndex !== null && selectedSeatIndex >= 0 && selectedSeatIndex < seats.length) {
        btn.classList.remove('hidden');
        btn.innerHTML = `<svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg><span>Delete ${seats[selectedSeatIndex].seat_code} (Del)</span>`;
    } else {
        btn.classList.add('hidden');
    }
}

function deleteSelectedSeatFromButton() {
    if (selectedSeatIndex !== null && selectedSeatIndex >= 0 && selectedSeatIndex < seats.length) {
        deleteSeat(selectedSeatIndex);
    }
}

function getCanvasCoordinates(event) {
    const rect = canvas.getBoundingClientRect();
    const scaleX = canvas.width / rect.width;
    const scaleY = canvas.height / rect.height;

    const x = Math.round((event.clientX - rect.left) * scaleX);
    const y = Math.round((event.clientY - rect.top) * scaleY);
    return { x: Math.max(0, Math.min(refWidth, x)), y: Math.max(0, Math.min(refHeight, y)) };
}

function isPointInPolygon(point, polygon) {
    if (!polygon || polygon.length < 3) return false;
    let inside = false;
    for (let i = 0, j = polygon.length - 1; i < polygon.length; j = i++) {
        const xi = polygon[i][0], yi = polygon[i][1];
        const xj = polygon[j][0], yj = polygon[j][1];
        const intersect = ((yi > point.y) !== (yj > point.y)) &&
            (point.x < (xj - xi) * (point.y - yi) / (yj - yi) + xi);
        if (intersect) inside = !inside;
    }
    return inside;
}

function handleCanvasMouseMove(event) {
    mousePos = getCanvasCoordinates(event);
    document.getElementById('mouseCoordsLabel').innerText = `X: ${mousePos.x} | Y: ${mousePos.y}`;

    if (editorMode === 'SELECT') {
        let prevHover = hoveredSeatIndex;
        hoveredSeatIndex = null;
        for (let i = seats.length - 1; i >= 0; i--) {
            if (isPointInPolygon(mousePos, seats[i].polygon)) {
                hoveredSeatIndex = i;
                break;
            }
        }
        canvas.style.cursor = hoveredSeatIndex !== null ? 'pointer' : 'default';
        if (prevHover !== hoveredSeatIndex) {
            redrawCanvas();
        }
    } else {
        canvas.style.cursor = 'crosshair';
        if (currentPolygon.length > 0) {
            redrawCanvas();
        }
    }
}

function handleCanvasMouseDown(event) {
    if (event.button !== 0) return; // Left click only
    const pt = getCanvasCoordinates(event);

    if (editorMode === 'SELECT') {
        let clickedSeatIdx = null;
        for (let i = seats.length - 1; i >= 0; i--) {
            if (isPointInPolygon(pt, seats[i].polygon)) {
                clickedSeatIdx = i;
                break;
            }
        }
        selectSeatIndex(clickedSeatIdx);
        return;
    }

    // DRAW MODE:
    // If clicking close to start point and length >= 3, auto complete polygon
    if (currentPolygon.length >= 3) {
        const start = currentPolygon[0];
        const dist = Math.hypot(pt.x - start[0], pt.y - start[1]);
        if (dist < 20) {
            completeCurrentPolygon();
            return;
        }
    }

    currentPolygon.push([pt.x, pt.y]);
    isDrawing = true;
    setDrawingStatus(`DRAWING (${currentPolygon.length} PTS)`, 'text-amber-400');
    redrawCanvas();
}

function handleCanvasDblClick(event) {
    if (editorMode === 'DRAW' && currentPolygon.length >= 3) {
        completeCurrentPolygon();
    }
}

function handleGlobalKeydown(event) {
    if (event.target.tagName === 'INPUT' || event.target.tagName === 'TEXTAREA' || event.target.tagName === 'SELECT') {
        return;
    }

    const key = event.key;

    if ((event.ctrlKey || event.metaKey) && (key === 'z' || key === 'Z')) {
        event.preventDefault();
        undoLastPoint();
        return;
    }

    if (key === 'v' || key === 'V') {
        event.preventDefault();
        setEditorMode('SELECT');
        return;
    }

    if (key === 'd' || key === 'D' || key === 'p' || key === 'P') {
        event.preventDefault();
        setEditorMode('DRAW');
        return;
    }

    if (key === 'Escape') {
        if (currentPolygon.length > 0) {
            clearCurrentPolygon();
        } else {
            selectSeatIndex(null);
        }
        return;
    }

    if (key === 'Enter' && currentPolygon.length >= 3) {
        completeCurrentPolygon();
        return;
    }

    if (key === 'Delete' || key === 'Backspace') {
        if (selectedSeatIndex !== null && selectedSeatIndex >= 0 && selectedSeatIndex < seats.length) {
            event.preventDefault();
            deleteSeat(selectedSeatIndex);
        }
    }
}

function undoLastPoint() {
    if (currentPolygon.length > 0) {
        currentPolygon.pop();
        if (currentPolygon.length === 0) {
            isDrawing = false;
            setDrawingStatus(editorMode === 'DRAW' ? 'DRAW MODE (CLICK TO PIN POINTS)' : 'READY', editorMode === 'DRAW' ? 'text-amber-400' : 'text-gray-400');
        } else {
            setDrawingStatus(`DRAWING (${currentPolygon.length} PTS)`, 'text-amber-400');
        }
        redrawCanvas();
    }
}

function clearCurrentPolygon() {
    currentPolygon = [];
    isDrawing = false;
    setDrawingStatus(editorMode === 'DRAW' ? 'DRAW MODE (CLICK TO PIN POINTS)' : 'READY', editorMode === 'DRAW' ? 'text-amber-400' : 'text-gray-400');
    redrawCanvas();
}

function completeCurrentPolygon() {
    if (currentPolygon.length === 0) {
        if (seats.length > 0) {
            alert(`Bạn đã tạo xong ${seats.length} chỗ ngồi trong danh sách.\n\n👉 Để lưu tất cả vào Hệ thống & Database, hãy nhấn nút màu xanh lá "Save All Seats to DB" ở góc trên bên phải màn hình!`);
        } else {
            alert('Vui lòng chuyển sang Draw Mode (phím D) và click chuột lên ảnh để vẽ các đỉnh đa giác cho bàn thi (tối thiểu 3 điểm).');
        }
        return;
    }

    if (currentPolygon.length < 3) {
        alert(`Đa giác chỗ ngồi cần tối thiểu 3 điểm (hình tam giác hoặc tứ giác). Hiện tại bạn mới chọn ${currentPolygon.length} điểm.`);
        return;
    }

    // Propose default seat code
    const nextIdx = seats.length + 1;
    const roomCode = rooms.find(r => r.id === currentRoomId)?.room_code || 'R101';
    const suggestedCode = `SEAT-${roomCode}-${String(nextIdx).padStart(2, '0')}`;
    const suggestedLabel = `Bàn ${nextIdx}`;

    document.getElementById('inputSeatCode').value = suggestedCode;
    document.getElementById('inputSeatLabel').value = suggestedLabel;
    document.getElementById('inputSeatEnabled').checked = true;

    document.getElementById('seatModal').classList.remove('hidden');
    document.getElementById('seatModal').classList.add('flex');
    document.getElementById('inputSeatCode').focus();
}

function closeSeatModal(keepPolygon = false) {
    document.getElementById('seatModal').classList.add('hidden');
    document.getElementById('seatModal').classList.remove('flex');
    if (!keepPolygon) {
        clearCurrentPolygon();
    }
}

function confirmSeatDetails() {
    const seatCode = document.getElementById('inputSeatCode').value.trim();
    const seatLabel = document.getElementById('inputSeatLabel').value.trim();
    const isEnabled = document.getElementById('inputSeatEnabled').checked;

    if (!seatCode) {
        alert('Please enter a unique Seat Code.');
        return;
    }

    const newSeat = {
        id: `temp_${Date.now()}`,
        seat_code: seatCode,
        seat_label: seatLabel || seatCode,
        polygon: [...currentPolygon],
        enabled: isEnabled
    };

    seats.push(newSeat);
    selectedSeatIndex = seats.length - 1;
    currentPolygon = [];
    isDrawing = false;

    closeSeatModal(true);
    setDrawingStatus('SEAT ADDED (UNSAVED)', 'text-amber-400');
    updateDeleteSelectedButton();
    renderSeatList();
    redrawCanvas();
}

// ==============================================================================
// 4. Canvas Rendering Engine (Reference Frame + Polygons + Labels)
// ==============================================================================
function redrawCanvas() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // 1. Draw Reference Image
    if (referenceImage) {
        ctx.drawImage(referenceImage, 0, 0, canvas.width, canvas.height);
    } else {
        ctx.fillStyle = '#0f172a';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
    }

    // 2. Draw Configured Seats
    seats.forEach((seat, idx) => {
        const isSelected = selectedSeatIndex === idx;
        const isHovered = (editorMode === 'SELECT' && hoveredSeatIndex === idx && !isSelected);
        const poly = seat.polygon;
        if (!poly || poly.length < 3) return;

        ctx.beginPath();
        ctx.moveTo(poly[0][0], poly[0][1]);
        for (let i = 1; i < poly.length; i++) {
            ctx.lineTo(poly[i][0], poly[i][1]);
        }
        ctx.closePath();

        // Styling
        if (!seat.enabled) {
            ctx.fillStyle = 'rgba(100, 116, 139, 0.25)';
            ctx.strokeStyle = '#64748b';
            ctx.lineWidth = 1.5;
            ctx.setLineDash([4, 4]);
        } else if (isSelected) {
            ctx.fillStyle = 'rgba(6, 182, 212, 0.35)';
            ctx.strokeStyle = '#22d3ee';
            ctx.lineWidth = 3;
            ctx.setLineDash([]);
        } else if (isHovered) {
            ctx.fillStyle = 'rgba(34, 211, 238, 0.22)';
            ctx.strokeStyle = '#38bdf8';
            ctx.lineWidth = 2.5;
            ctx.setLineDash([]);
        } else {
            ctx.fillStyle = 'rgba(16, 185, 129, 0.25)';
            ctx.strokeStyle = '#10b981';
            ctx.lineWidth = 2;
            ctx.setLineDash([]);
        }

        ctx.fill();
        ctx.stroke();
        ctx.setLineDash([]);

        // Draw point handles
        poly.forEach(([px, py]) => {
            ctx.beginPath();
            ctx.arc(px, py, isSelected ? 4.5 : (isHovered ? 4 : 3), 0, Math.PI * 2);
            ctx.fillStyle = isSelected ? '#38bdf8' : (isHovered ? '#67e8f9' : '#34d399');
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1;
            ctx.stroke();
        });

        // Draw Seat Badge Label at centroid
        const cx = poly.reduce((acc, p) => acc + p[0], 0) / poly.length;
        const cy = poly.reduce((acc, p) => acc + p[1], 0) / poly.length;

        drawSeatBadge(seat.seat_code, cx, cy, isSelected, seat.enabled, isHovered);
    });

    // 3. Draw Active In-Progress Polygon
    if (currentPolygon.length > 0) {
        ctx.beginPath();
        ctx.moveTo(currentPolygon[0][0], currentPolygon[0][1]);
        for (let i = 1; i < currentPolygon.length; i++) {
            ctx.lineTo(currentPolygon[i][0], currentPolygon[i][1]);
        }

        // Live rubberband line to cursor
        ctx.lineTo(mousePos.x, mousePos.y);

        ctx.fillStyle = 'rgba(234, 179, 8, 0.2)';
        ctx.fill();
        ctx.strokeStyle = '#facc15';
        ctx.lineWidth = 2;
        ctx.setLineDash([5, 3]);
        ctx.stroke();
        ctx.setLineDash([]);

        // Point handles for in-progress polygon
        currentPolygon.forEach(([px, py], pidx) => {
            ctx.beginPath();
            ctx.arc(px, py, 5, 0, Math.PI * 2);
            ctx.fillStyle = pidx === 0 ? '#ef4444' : '#fbbf24'; // First point highlighted red
            ctx.fill();
            ctx.strokeStyle = '#ffffff';
            ctx.lineWidth = 1.5;
            ctx.stroke();
        });
    }
}

function drawSeatBadge(text, x, y, isSelected, isEnabled, isHovered = false) {
    ctx.font = 'bold 12px "JetBrains Mono", monospace';
    const textWidth = ctx.measureText(text).width;
    const pad = 6;
    const boxW = textWidth + pad * 2;
    const boxH = 20;

    const bx = x - boxW / 2;
    const by = y - boxH / 2;

    ctx.fillStyle = !isEnabled ? 'rgba(30, 41, 59, 0.9)' : (isSelected ? 'rgba(6, 182, 212, 0.95)' : (isHovered ? 'rgba(14, 116, 144, 0.9)' : 'rgba(15, 23, 42, 0.85)'));
    ctx.strokeStyle = isSelected ? '#ffffff' : (isHovered ? '#67e8f9' : (isEnabled ? '#10b981' : '#64748b'));
    ctx.lineWidth = 1.5;

    // Rounded rectangle
    roundRect(ctx, bx, by, boxW, boxH, 4, true, true);

    ctx.fillStyle = '#ffffff';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, x, y);
}

function roundRect(ctx, x, y, width, height, radius, fill, stroke) {
    ctx.beginPath();
    ctx.moveTo(x + radius, y);
    ctx.lineTo(x + width - radius, y);
    ctx.quadraticCurveTo(x + width, y, x + width, y + radius);
    ctx.lineTo(x + width, y + height - radius);
    ctx.quadraticCurveTo(x + width, y + height, x + width - radius, y + height);
    ctx.lineTo(x + radius, y + height);
    ctx.quadraticCurveTo(x, y + height, x, y + height - radius);
    ctx.lineTo(x, y + radius);
    ctx.quadraticCurveTo(x, y, x + radius, y);
    ctx.closePath();
    if (fill) ctx.fill();
    if (stroke) ctx.stroke();
}

function setDrawingStatus(text, colorCls) {
    const badge = document.getElementById('drawingStatusBadge');
    if (badge) {
        badge.innerText = text;
        badge.className = `text-[11px] px-2 py-0.5 rounded bg-gray-800 font-mono ${colorCls}`;
    }
}

// ==============================================================================
// 5. Seat List Sidebar & Management
// ==============================================================================
function renderSeatList() {
    const container = document.getElementById('seatItemsList');
    const badge = document.getElementById('seatCountBadge');
    if (!container) return;

    badge.innerText = `${seats.length} Seats`;

    if (seats.length === 0) {
        container.innerHTML = `<div class="p-6 text-center text-xs text-gray-500">No seats configured. Switch to Draw Mode (D) to draw.</div>`;
        return;
    }

    container.innerHTML = seats.map((s, idx) => {
        const isSelected = selectedSeatIndex === idx;
        const activeClass = isSelected ? 'active' : '';
        const enabledText = s.enabled ? 'Enabled' : 'Disabled';
        const enabledBadgeCls = s.enabled ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/30' : 'bg-gray-800 text-gray-400 border-gray-700';

        return `
        <div id="seat-item-${idx}" onclick="selectSeatIndex(${idx})" class="p-3 rounded-xl bg-gray-900/80 border border-gray-800 seat-item ${activeClass} transition cursor-pointer flex items-center justify-between gap-2">
            <div>
                <div class="flex items-center gap-2">
                    <span class="font-bold text-xs font-mono text-white">${s.seat_code}</span>
                    <span class="text-[10px] px-1.5 py-0.5 rounded border font-mono ${enabledBadgeCls}">${enabledText}</span>
                </div>
                <div class="text-[11px] text-gray-400 mt-0.5">${s.seat_label || 'No description'}</div>
                <div class="text-[10px] text-gray-500 font-mono mt-0.5">${s.polygon.length} points polygon</div>
            </div>

            <div class="flex items-center gap-1" onclick="event.stopPropagation()">
                <button onclick="toggleSeatEnabled(${idx})" class="p-1.5 rounded hover:bg-gray-800 text-gray-400 hover:text-cyan-300 transition" title="Toggle Enable/Disable">
                    ${s.enabled ? '🟢' : '⚪'}
                </button>
                <button onclick="deleteSeat(${idx})" class="p-1.5 rounded hover:bg-red-950 text-gray-400 hover:text-red-400 transition" title="Delete Seat (Del)">
                    <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/></svg>
                </button>
            </div>
        </div>
        `;
    }).join('');
}

function selectSeatIndex(idx) {
    if (idx === null) {
        selectedSeatIndex = null;
    } else if (selectedSeatIndex === idx) {
        selectedSeatIndex = null; // Toggle off
    } else {
        selectedSeatIndex = idx;
    }
    updateDeleteSelectedButton();
    renderSeatList();
    redrawCanvas();

    if (selectedSeatIndex !== null) {
        const item = document.getElementById(`seat-item-${selectedSeatIndex}`);
        if (item) item.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        setDrawingStatus(`SELECTED ${seats[selectedSeatIndex].seat_code} (PRESS DEL TO DELETE)`, 'text-cyan-400');
    } else {
        if (editorMode === 'SELECT') {
            setDrawingStatus('SELECT MODE (CLICK SEAT)', 'text-cyan-400');
        } else {
            setDrawingStatus('DRAW MODE (CLICK TO PIN POINTS)', 'text-amber-400');
        }
    }
}

function toggleSeatEnabled(idx) {
    seats[idx].enabled = !seats[idx].enabled;
    renderSeatList();
    redrawCanvas();
}

async function deleteSeat(idx) {
    if (idx < 0 || idx >= seats.length) return;
    const seatToDelete = seats[idx];

    if (!confirm(`Are you sure you want to delete Seat ${seatToDelete.seat_code}?`)) {
        return;
    }

    // If seat was loaded from database, delete from database immediately
    if (seatToDelete.id && !seatToDelete.id.startsWith('temp_')) {
        try {
            const res = await fetch(`/api/v1/seats/${seatToDelete.id}`, { method: 'DELETE' });
            if (!res.ok) {
                // Fallback delete by code
                await fetch(`/api/v1/data/seats/by-code/${encodeURIComponent(seatToDelete.seat_code)}`, { method: 'DELETE' });
            }
        } catch (e) {
            console.warn('Failed to delete seat from DB:', e);
        }
    } else if (seatToDelete.seat_code) {
        // Fallback delete by code
        try {
            await fetch(`/api/v1/data/seats/by-code/${encodeURIComponent(seatToDelete.seat_code)}`, { method: 'DELETE' });
        } catch (e) {
            console.warn('Failed to delete seat by code:', e);
        }
    }

    seats.splice(idx, 1);
    if (selectedSeatIndex === idx) {
        selectedSeatIndex = null;
    } else if (selectedSeatIndex > idx) {
        selectedSeatIndex--;
    }

    updateDeleteSelectedButton();
    renderSeatList();
    redrawCanvas();
    setDrawingStatus(`DELETED ${seatToDelete.seat_code}`, 'text-rose-400');
}

async function clearAllSeats() {
    if (seats.length === 0) return;
    if (!confirm(`Are you sure you want to remove all ${seats.length} seats from the current view and database?`)) {
        return;
    }

    for (const s of seats) {
        if (s.id && !s.id.startsWith('temp_')) {
            try {
                await fetch(`/api/v1/seats/${s.id}`, { method: 'DELETE' });
            } catch (e) {}
        }
    }

    seats = [];
    selectedSeatIndex = null;
    updateDeleteSelectedButton();
    renderSeatList();
    redrawCanvas();
    setDrawingStatus('ALL SEATS CLEARED', 'text-amber-400');
}

// ==============================================================================
// 6. DB Persistence & API Synchronization
// ==============================================================================
async function reloadSeatsFromDB() {
    if (!currentRoomId) return;
    setDrawingStatus('FETCHING SEATS...', 'text-amber-400');

    try {
        const res = await fetch(`/api/v1/rooms/${currentRoomId}/seats`);
        if (!res.ok) throw new Error('Failed to fetch seats');
        const dbSeats = await res.json();

        seats = dbSeats.map(s => {
            let poly = [];
            if (typeof s.polygon_json === 'string') {
                try { poly = JSON.parse(s.polygon_json); } catch(e) { poly = []; }
            } else if (Array.isArray(s.polygon_json)) {
                poly = s.polygon_json;
            }

            // If coordinates are in dict format [{"x":..., "y":...}]
            if (poly.length > 0 && typeof poly[0] === 'object' && !Array.isArray(poly[0])) {
                poly = poly.map(p => [p.x, p.y]);
            }

            return {
                id: s.id,
                seat_code: s.seat_code,
                seat_label: s.seat_label,
                polygon: poly,
                enabled: s.enabled
            };
        });

        selectedSeatIndex = null;
        renderSeatList();
        redrawCanvas();
        setDrawingStatus('SEATS SYNCED (DB)', 'text-emerald-400');
    } catch (err) {
        console.error('Error reloading seats:', err);
        setDrawingStatus('SYNC ERROR', 'text-red-400');
    }
}

async function saveAllSeatsToDB() {
    if (!currentRoomId) {
        alert('Please select an active Exam Room first.');
        return;
    }

    if (seats.length === 0) {
        alert('No seats to save. Draw seats on canvas first.');
        return;
    }

    setDrawingStatus('SAVING TO DB...', 'text-amber-400');

    const payload = {
        room_id: currentRoomId,
        camera_id: currentCameraId,
        seats: seats.map(s => ({
            room_id: currentRoomId,
            camera_id: currentCameraId,
            seat_code: s.seat_code,
            seat_label: s.seat_label || s.seat_code,
            polygon_json: s.polygon,
            enabled: s.enabled
        }))
    };

    try {
        const res = await fetch(`/api/v1/rooms/${currentRoomId}/seats/bulk`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });

        if (!res.ok) throw new Error('Database bulk upsert failed');
        const savedSeats = await res.json();

        alert(`Successfully saved ${savedSeats.length} Seat ROIs to Database for room!`);
        setDrawingStatus('SAVED TO CSDL', 'text-emerald-400');
        reloadSeatsFromDB();
    } catch (err) {
        alert('Failed to save seats: ' + err.message);
        setDrawingStatus('SAVE ERROR', 'text-red-400');
    }
}

function copySeatsJSON() {
    if (seats.length === 0) {
        alert('No seats configured to copy.');
        return;
    }
    const jsonStr = JSON.stringify(seats, null, 2);
    navigator.clipboard.writeText(jsonStr).then(() => {
        alert(`Copied ${seats.length} Seat ROIs JSON configuration to clipboard!`);
    }).catch(() => {
        prompt('Copy Seat Configuration JSON:', jsonStr);
    });
}
