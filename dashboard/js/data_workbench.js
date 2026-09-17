/**
 * VIGIL AI — Human Data Operations Workbench Client Script
 * Sprint 2: Observation Quality & Validation Data
 */

const API_BASE = "/api/v1/data";

// State
let currentTab = "overview";
let currentImageIndex = 0;
let imageAssetsList = [];
let currentAssetDetail = null;
let selectedBboxIndex = 0;
let currentVideoAsset = null;
let videoEpisodes = [];
let activeRole = "AI_ML_ENGINEER";
let availableSeats = [];
let showSeatRois = true;
let showSeatLabels = true;
let hoveredSeatCode = null;
let selectedSeatCode = null;

// DOM Loaded
document.addEventListener("DOMContentLoaded", () => {
  initTabs();
  initRoleSwitcher();
  loadSummary();
  initImageReviewShortcuts();
  initTimelineControls();
  initVideoOverlayCanvas();
  initVideoHotkeys();
  loadAvailableSeats();
});

// Toast Notifications
function showToast(message, type = "info") {
  const container = document.getElementById("toast-container");
  if (!container) return;
  const toast = document.createElement("div");
  toast.className = "toast";
  if (type === "success") toast.style.borderLeftColor = "var(--accent-green)";
  if (type === "error") toast.style.borderLeftColor = "var(--accent-rose)";
  if (type === "warning") toast.style.borderLeftColor = "var(--accent-amber)";
  toast.innerHTML = `<span>${message}</span>`;
  container.appendChild(toast);
  setTimeout(() => {
    toast.style.opacity = "0";
    toast.style.transition = "opacity 0.3s";
    setTimeout(() => toast.remove(), 300);
  }, 3500);
}

// Tab Switching
function initTabs() {
  document.querySelectorAll(".tab-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      const target = btn.dataset.tab;
      switchTab(target);
    });
  });
}

function switchTab(tabId) {
  currentTab = tabId;
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === tabId));
  document.querySelectorAll(".tab-pane").forEach((p) => p.classList.toggle("active", p.id === `${tabId}-pane`));

  if (tabId === "overview") loadSummary();
  if (tabId === "images") loadImagesList();
  if (tabId === "videos") {
    loadVideosList();
    loadAvailableSeats();
    setTimeout(renderVideoOverlay, 150);
  }
  if (tabId === "calibration") runCalibrationCheck();
  if (tabId === "events") loadEventReviewQueue();
  if (tabId === "export") loadDatasetsList();
  if (tabId === "staged") loadStagedSessions();
}

function initRoleSwitcher() {
  const roleSelect = document.getElementById("active-role-select");
  if (roleSelect) {
    roleSelect.addEventListener("change", (e) => {
      activeRole = e.target.value;
      showToast(`Active Role switched to ${activeRole}`, "info");
    });
  }
}

// -----------------------------------------------------------------------------
// 1. OVERVIEW & SUMMARY
// -----------------------------------------------------------------------------
async function loadSummary() {
  try {
    const res = await fetch(`${API_BASE}/summary`);
    const data = await res.json();

    document.getElementById("stat-total-images").innerText = data.total_images || 0;
    document.getElementById("stat-audit-progress").innerText = `${data.audit_percentage || 0}%`;
    document.getElementById("stat-audited-count").innerText = `${data.audited_images || 0} / ${data.total_images || 0} reviewed`;
    document.getElementById("stat-total-episodes").innerText = data.total_episodes || 0;
    document.getElementById("stat-human-episodes").innerText = `${data.human_episodes_count || 0} Human, ${data.ai_proposals_count || 0} AI`;
    document.getElementById("stat-pending-events").innerText = data.pending_events_count || 0;
    document.getElementById("stat-dataset-versions").innerText = data.dataset_versions_count || 0;

    // Splits breakdown
    const splitsContainer = document.getElementById("splits-breakdown");
    if (splitsContainer && data.splits) {
      splitsContainer.innerHTML = Object.entries(data.splits)
        .map(([k, v]) => `<span class="badge-tag badge-blue">${k}: ${v}</span>`)
        .join(" ");
    }
  } catch (err) {
    console.error("Error loading summary:", err);
  }
}

async function triggerIndexAllAssets() {
  showToast("Scanning and indexing local datasets...", "info");
  try {
    const res = await fetch(`${API_BASE}/index-assets`, { method: "POST" });
    const data = await res.json();
    showToast(`Indexing complete! Indexed ${data.image_indexing.indexed_new} new images.`, "success");
    loadSummary();
  } catch (err) {
    showToast("Failed to index assets", "error");
  }
}

// -----------------------------------------------------------------------------
// 2. IMAGE AUDIT & RAPID REVIEW
// -----------------------------------------------------------------------------
async function loadImagesList() {
  const splitFilter = document.getElementById("img-split-filter")?.value || "";
  const statusFilter = document.getElementById("img-status-filter")?.value || "";

  let url = `${API_BASE}/images?limit=100`;
  if (splitFilter) url += `&split=${encodeURIComponent(splitFilter)}`;
  if (statusFilter) url += `&audit_status=${encodeURIComponent(statusFilter)}`;

  try {
    const res = await fetch(url);
    const data = await res.json();
    imageAssetsList = data.items || [];
    renderImageCarousel();

    if (imageAssetsList.length > 0) {
      loadImageDetail(imageAssetsList[0].id);
    } else {
      clearImageCanvas();
    }
  } catch (err) {
    console.error("Error loading images:", err);
  }
}

function renderImageCarousel() {
  const container = document.getElementById("image-carousel");
  if (!container) return;

  container.innerHTML = imageAssetsList
    .map((item, idx) => `
      <div class="carousel-item ${idx === currentImageIndex ? 'active' : ''}" onclick="selectImageByIndex(${idx})">
        <img src="${API_BASE}/images/${item.id}/crop?padding_ratio=0.2&target_size=100" class="carousel-thumb" alt="${item.file_name}" loading="lazy" />
        <div class="carousel-label">${item.file_name.substring(0, 14)}...</div>
        <span class="badge-tag ${item.audit_status === 'AUDITED' ? 'badge-green' : (item.audit_status === 'FLAGGED' ? 'badge-amber' : 'badge-blue')}">${item.audit_status}</span>
      </div>
    `)
    .join("");
}

function scrollCarousel(direction) {
  const container = document.getElementById("image-carousel");
  if (container) {
    container.scrollBy({ left: direction * 350, behavior: "smooth" });
  }
}

function selectImageByIndex(idx) {
  if (idx < 0 || idx >= imageAssetsList.length) return;
  currentImageIndex = idx;
  renderImageCarousel();
  loadImageDetail(imageAssetsList[idx].id);

  // Smooth scroll active thumbnail into view
  setTimeout(() => {
    const activeItem = document.querySelector(".carousel-item.active");
    if (activeItem) {
      activeItem.scrollIntoView({ behavior: "smooth", block: "nearest", inline: "center" });
    }
  }, 50);
}

async function loadImageDetail(assetId) {
  try {
    const res = await fetch(`${API_BASE}/images/${assetId}`);
    currentAssetDetail = await res.json();
    selectedBboxIndex = 0;
    renderImageCanvas();
    updateBboxInspector();
  } catch (err) {
    console.error("Error loading image detail:", err);
  }
}

function renderImageCanvas() {
  const canvas = document.getElementById("image-audit-canvas");
  if (!canvas || !currentAssetDetail) return;
  const ctx = canvas.getContext("2d");

  const img = new Image();
  img.crossOrigin = "anonymous";
  img.src = `${API_BASE}/images/${currentAssetDetail.id}/file`;
  img.onload = () => {
    canvas.width = img.naturalWidth || 640;
    canvas.height = img.naturalHeight || 640;
    ctx.drawImage(img, 0, 0);

    // Draw bboxes
    const bboxes = getEffectiveBboxes();
    bboxes.forEach((b, idx) => {
      const isSelected = idx === selectedBboxIndex;
      const xc = b.bbox[0] * canvas.width;
      const yc = b.bbox[1] * canvas.height;
      const bw = b.bbox[2] * canvas.width;
      const bh = b.bbox[3] * canvas.height;
      const x1 = xc - bw / 2;
      const y1 = yc - bh / 2;

      ctx.strokeStyle = isSelected ? "#3b82f6" : "#10b981";
      ctx.lineWidth = isSelected ? 4 : 2;
      ctx.strokeRect(x1, y1, bw, bh);

      // Label badge
      const lbl = b.reviewed_class || b.observable_label || b.class_name;
      ctx.fillStyle = isSelected ? "rgba(59, 130, 246, 0.9)" : "rgba(16, 185, 129, 0.8)";
      ctx.fillRect(x1, y1 - 22, ctx.measureText(lbl).width + 16, 22);
      ctx.fillStyle = "#ffffff";
      ctx.font = "bold 12px sans-serif";
      ctx.fillText(lbl, x1 + 6, y1 - 6);
    });
  };
}

function getEffectiveBboxes() {
  if (!currentAssetDetail) return [];
  if (currentAssetDetail.revisions && currentAssetDetail.revisions.length > 0) {
    return currentAssetDetail.revisions;
  }
  return currentAssetDetail.raw_bboxes || [];
}

function updateBboxInspector() {
  if (!currentAssetDetail) return;
  const bboxes = getEffectiveBboxes();
  if (bboxes.length === 0) {
    document.getElementById("bbox-inspector-panel").innerHTML = "<p class='text-secondary'>No bounding boxes detected on this image.</p>";
    return;
  }

  const currentBox = bboxes[selectedBboxIndex] || bboxes[0];
  const origClass = currentBox.original_class || currentBox.class_name || "no cheating";
  const reviewedClass = currentBox.reviewed_class || currentBox.observable_label || "NORMAL_WRITING";

  document.getElementById("orig-class-display").innerText = origClass;
  const classSelect = document.getElementById("reviewed-class-select");
  if (classSelect) classSelect.value = reviewedClass;

  const ambCheckbox = document.getElementById("is-ambiguous-checkbox");
  if (ambCheckbox) ambCheckbox.checked = currentBox.is_ambiguous || false;

  const rejCheckbox = document.getElementById("is-rejected-checkbox");
  if (rejCheckbox) rejCheckbox.checked = currentBox.is_rejected || false;

  // Update dynamic actor crop image
  const cropImg = document.getElementById("actor-crop-preview-img");
  if (cropImg) {
    cropImg.src = `${API_BASE}/images/${currentAssetDetail.id}/crop?bbox_index=${selectedBboxIndex}&padding_ratio=0.15&target_size=224&t=${Date.now()}`;
  }
}

async function saveCurrentImageReview(advanceNext = false) {
  if (!currentAssetDetail) return;

  const bboxes = getEffectiveBboxes();
  const currentBox = bboxes[selectedBboxIndex] || bboxes[0];

  const reviewedClass = document.getElementById("reviewed-class-select").value;
  const isAmbiguous = document.getElementById("is-ambiguous-checkbox").checked;
  const isRejected = document.getElementById("is-rejected-checkbox").checked;
  const auditNotes = document.getElementById("audit-notes-input").value;

  const payload = {
    revisions: [
      {
        bbox_index: selectedBboxIndex,
        original_class: currentBox.original_class || currentBox.class_name || "no cheating",
        reviewed_class: reviewedClass,
        bbox: currentBox.bbox || [0.5, 0.5, 0.4, 0.6],
        is_ambiguous: isAmbiguous,
        is_rejected: isRejected,
        audit_notes: auditNotes,
      },
    ],
    overall_status: isAmbiguous || isRejected ? "FLAGGED" : "AUDITED",
    reviewer_id: activeRole,
  };

  try {
    const res = await fetch(`${API_BASE}/images/${currentAssetDetail.id}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify(payload),
    });
    if (res.ok) {
      showToast("Review saved!", "success");
      // Update local asset list status badge
      if (imageAssetsList[currentImageIndex]) {
        imageAssetsList[currentImageIndex].audit_status = payload.overall_status;
        renderImageCarousel();
      }
      if (advanceNext) {
        selectImageByIndex(currentImageIndex + 1);
      } else {
        loadImageDetail(currentAssetDetail.id);
      }
    }
  } catch (err) {
    showToast("Failed to save image review", "error");
  }
}

function initImageReviewShortcuts() {
  window.addEventListener("keydown", (e) => {
    if (currentTab !== "images") return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    if (e.key === "a" || e.key === "A") {
      saveCurrentImageReview(true);
    } else if (e.key === "ArrowRight") {
      selectImageByIndex(currentImageIndex + 1);
    } else if (e.key === "ArrowLeft") {
      selectImageByIndex(currentImageIndex - 1);
    } else if (e.key === "x" || e.key === "X") {
      const cb = document.getElementById("is-ambiguous-checkbox");
      if (cb) cb.checked = !cb.checked;
    } else if (e.key === "n" || e.key === "N") {
      document.getElementById("reviewed-class-select").value = "NORMAL_WRITING";
    } else if (e.key === "s" || e.key === "S") {
      document.getElementById("reviewed-class-select").value = "HEAD_TURN_SIDE";
    } else if (e.key === "p" || e.key === "P") {
      document.getElementById("reviewed-class-select").value = "PHONE_OR_DEVICE_INTERACTION";
    }
  });
}

function clearImageCanvas() {
  const canvas = document.getElementById("image-audit-canvas");
  if (!canvas) return;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
}

// -----------------------------------------------------------------------------
// 3. VIDEO TEMPORAL EPISODE & AI DIFF
// -----------------------------------------------------------------------------
function toggleSeatRoiOverlay(checked) {
  showSeatRois = checked;
  renderVideoOverlay();
}

function toggleSeatLabels(checked) {
  showSeatLabels = checked;
  renderVideoOverlay();
}

async function loadAvailableSeats() {
  try {
    const res = await fetch(`${API_BASE}/seats`);
    const data = await res.json();
    availableSeats = data.seats || [];

    if (availableSeats.length === 0) {
      // Sensible classroom defaults based on 1280x720 video
      availableSeats = [
        { seat_code: "SEAT-101-01", seat_label: "Bàn 1 Dãy Trái", polygon: [[108, 382], [283, 382], [283, 544], [108, 544]] },
        { seat_code: "SEAT-101-02", seat_label: "Bàn 1 Dãy Giữa", polygon: [[509, 478], [677, 478], [677, 629], [509, 629]] },
        { seat_code: "SEAT-101-03", seat_label: "Bàn 1 Dãy Phải", polygon: [[1000, 401], [1146, 401], [1146, 646], [1000, 646]] },
        { seat_code: "SEAT-101-04", seat_label: "Bàn 2 Dãy Trái", polygon: [[212, 277], [360, 277], [360, 400], [212, 400]] },
        { seat_code: "SEAT-101-05", seat_label: "Bàn 2 Dãy Giữa", polygon: [[491, 316], [629, 316], [629, 478], [491, 478]] },
      ];
    }

    populateSeatDropdowns();
    renderVideoOverlay();
  } catch (err) {
    console.error("Error loading seats:", err);
  }
}

function populateSeatDropdowns() {
  const seatSelect = document.getElementById("ep-seat-code");
  const neighborSelect = document.getElementById("ep-neighbor-code");
  if (!seatSelect) return;

  seatSelect.innerHTML = availableSeats
    .map(s => `<option value="${s.seat_code}">${s.seat_label ? s.seat_label + ' (' + s.seat_code + ')' : s.seat_code}</option>`)
    .join("");

  if (neighborSelect) {
    neighborSelect.innerHTML = '<option value="">None</option>' + availableSeats
      .map(s => `<option value="${s.seat_code}">${s.seat_label ? s.seat_label + ' (' + s.seat_code + ')' : s.seat_code}</option>`)
      .join("");
  }

  if (availableSeats.length > 0 && !selectedSeatCode) {
    selectedSeatCode = availableSeats[0].seat_code;
    seatSelect.value = selectedSeatCode;
  }
}

function isPointInPoly(pt, poly) {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const xi = poly[i][0], yi = poly[i][1];
    const xj = poly[j][0], yj = poly[j][1];
    const intersect = ((yi > pt[1]) !== (yj > pt[1])) &&
      (pt[0] < (xj - xi) * (pt[1] - yi) / (yj - yi) + xi);
    if (intersect) inside = !inside;
  }
  return inside;
}

function getVideoRenderBox(video, canvas) {
  const containerWidth = canvas.width || 800;
  const containerHeight = canvas.height || 450;
  const videoWidth = video.videoWidth || 1280;
  const videoHeight = video.videoHeight || 720;

  const videoRatio = videoWidth / videoHeight;
  const containerRatio = containerWidth / containerHeight;

  let renderWidth, renderHeight, offsetX, offsetY;
  if (containerRatio > videoRatio) {
    renderHeight = containerHeight;
    renderWidth = containerHeight * videoRatio;
    offsetX = (containerWidth - renderWidth) / 2;
    offsetY = 0;
  } else {
    renderWidth = containerWidth;
    renderHeight = containerWidth / videoRatio;
    offsetX = 0;
    offsetY = (containerHeight - renderHeight) / 2;
  }

  return { offsetX, offsetY, renderWidth, renderHeight, videoWidth, videoHeight };
}

function findSeatAtPoint(mouseX, mouseY, canvas, video) {
  const box = getVideoRenderBox(video, canvas);
  for (const seat of availableSeats) {
    if (!seat.polygon || seat.polygon.length < 3) continue;
    const scaledPoly = seat.polygon.map(pt => {
      let nx = pt[0], ny = pt[1];
      if (nx > 1.0 || ny > 1.0) {
        nx = nx / (box.videoWidth || 1280);
        ny = ny / (box.videoHeight || 720);
      }
      return [
        box.offsetX + nx * box.renderWidth,
        box.offsetY + ny * box.renderHeight,
      ];
    });

    if (isPointInPoly([mouseX, mouseY], scaledPoly)) {
      return seat.seat_code;
    }
  }
  return null;
}

function renderVideoOverlay() {
  const canvas = document.getElementById("video-overlay-canvas");
  const video = document.getElementById("workbench-video-player");
  if (!canvas || !video) return;

  const rect = video.getBoundingClientRect();
  if (rect.width > 0 && rect.height > 0 && (canvas.width !== Math.round(rect.width) || canvas.height !== Math.round(rect.height))) {
    canvas.width = Math.round(rect.width);
    canvas.height = Math.round(rect.height);
  }

  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  if (!showSeatRois || availableSeats.length === 0) return;

  const box = getVideoRenderBox(video, canvas);

  availableSeats.forEach(seat => {
    if (!seat.polygon || seat.polygon.length < 3) return;

    const isSelected = seat.seat_code === selectedSeatCode;
    const isHovered = seat.seat_code === hoveredSeatCode;

    const scaledPoly = seat.polygon.map(pt => {
      let nx = pt[0], ny = pt[1];
      if (nx > 1.0 || ny > 1.0) {
        nx = nx / (box.videoWidth || 1280);
        ny = ny / (box.videoHeight || 720);
      }
      return [
        box.offsetX + nx * box.renderWidth,
        box.offsetY + ny * box.renderHeight,
      ];
    });

    // Draw Polygon
    ctx.beginPath();
    ctx.moveTo(scaledPoly[0][0], scaledPoly[0][1]);
    for (let i = 1; i < scaledPoly.length; i++) {
      ctx.lineTo(scaledPoly[i][0], scaledPoly[i][1]);
    }
    ctx.closePath();

    if (isSelected) {
      ctx.fillStyle = "rgba(245, 158, 11, 0.32)";
      ctx.strokeStyle = "#f59e0b";
      ctx.lineWidth = 3;
      ctx.setLineDash([]);
    } else if (isHovered) {
      ctx.fillStyle = "rgba(6, 182, 212, 0.35)";
      ctx.strokeStyle = "#06b6d4";
      ctx.lineWidth = 2.5;
      ctx.setLineDash([4, 2]);
    } else {
      ctx.fillStyle = "rgba(16, 185, 129, 0.16)";
      ctx.strokeStyle = "#10b981";
      ctx.lineWidth = 1.5;
      ctx.setLineDash([]);
    }

    ctx.fill();
    ctx.stroke();
    ctx.setLineDash([]);

    // Draw Seat Centroid Badge
    if (showSeatLabels) {
      let cx = 0, cy = 0;
      scaledPoly.forEach(p => { cx += p[0]; cy += p[1]; });
      cx /= scaledPoly.length;
      cy /= scaledPoly.length;

      const labelText = seat.seat_label ? `${seat.seat_label}` : seat.seat_code;
      ctx.font = isSelected ? "bold 11px sans-serif" : "10px sans-serif";
      const textMetrics = ctx.measureText(labelText);
      const textWidth = textMetrics.width;

      ctx.fillStyle = isSelected ? "rgba(245, 158, 11, 0.95)" : (isHovered ? "rgba(6, 182, 212, 0.95)" : "rgba(17, 24, 39, 0.88)");
      ctx.fillRect(cx - textWidth / 2 - 5, cy - 9, textWidth + 10, 18);
      ctx.strokeStyle = isSelected ? "#ffffff" : (isHovered ? "#ffffff" : "rgba(16, 185, 129, 0.8)");
      ctx.lineWidth = 1;
      ctx.strokeRect(cx - textWidth / 2 - 5, cy - 9, textWidth + 10, 18);

      ctx.fillStyle = "#ffffff";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(labelText, cx, cy);
    }
  });
}

function initVideoOverlayCanvas() {
  const video = document.getElementById("workbench-video-player");
  const canvas = document.getElementById("video-overlay-canvas");
  if (!video || !canvas) return;

  const updateCanvasSize = () => {
    const rect = video.getBoundingClientRect();
    if (rect.width > 0 && rect.height > 0) {
      canvas.width = Math.round(rect.width);
      canvas.height = Math.round(rect.height);
      renderVideoOverlay();
    }
  };

  video.addEventListener("loadedmetadata", updateCanvasSize);
  video.addEventListener("timeupdate", renderVideoOverlay);
  video.addEventListener("play", () => renderVideoOverlay());
  video.addEventListener("pause", () => renderVideoOverlay());
  video.addEventListener("seeked", () => renderVideoOverlay());
  window.addEventListener("resize", updateCanvasSize);

  const seatSelect = document.getElementById("ep-seat-code");
  if (seatSelect) {
    seatSelect.addEventListener("change", (e) => {
      selectedSeatCode = e.target.value;
      renderVideoOverlay();
    });
  }

  canvas.addEventListener("mousemove", (e) => {
    if (!showSeatRois || availableSeats.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = findSeatAtPoint(mouseX, mouseY, canvas, video);
    if (hit !== hoveredSeatCode) {
      hoveredSeatCode = hit;
      renderVideoOverlay();
      const indicator = document.getElementById("hovered-seat-indicator");
      if (indicator) {
        if (hoveredSeatCode) {
          const seatObj = availableSeats.find(s => s.seat_code === hoveredSeatCode);
          indicator.innerText = `Hovering: ${seatObj ? (seatObj.seat_label || seatObj.seat_code) : hoveredSeatCode} (Click to select)`;
        } else {
          indicator.innerText = "";
        }
      }
    }
  });

  canvas.addEventListener("mouseleave", () => {
    if (hoveredSeatCode !== null) {
      hoveredSeatCode = null;
      renderVideoOverlay();
      const indicator = document.getElementById("hovered-seat-indicator");
      if (indicator) indicator.innerText = "";
    }
  });

  canvas.addEventListener("click", (e) => {
    if (!showSeatRois || availableSeats.length === 0) return;
    const rect = canvas.getBoundingClientRect();
    const mouseX = e.clientX - rect.left;
    const mouseY = e.clientY - rect.top;

    const hit = findSeatAtPoint(mouseX, mouseY, canvas, video);
    if (hit) {
      selectedSeatCode = hit;
      const seatSelect = document.getElementById("ep-seat-code");
      if (seatSelect) seatSelect.value = hit;
      updateDeleteSeatButtonVisibility();
      showToast(`Selected ${hit} on video (Press Del to delete)`, "info");
      renderVideoOverlay();
    }
  });
}

function updateDeleteSeatButtonVisibility() {
  const btn = document.getElementById("delete-selected-seat-btn");
  if (!btn) return;
  if (selectedSeatCode) {
    btn.style.display = "inline-flex";
    btn.innerText = `🗑️ Delete ${selectedSeatCode} (Del)`;
  } else {
    btn.style.display = "none";
  }
}

async function deleteSelectedSeat(targetSeatCode) {
  const codeToDelete = targetSeatCode || selectedSeatCode;
  if (!codeToDelete) {
    showToast("Please click a Seat ROI on video first to select it.", "warning");
    return;
  }

  const seatObj = availableSeats.find((s) => s.seat_code === codeToDelete);
  const seatLabel = seatObj ? (seatObj.seat_label || seatObj.seat_code) : codeToDelete;

  if (!confirm(`Are you sure you want to permanently delete Seat ROI: ${seatLabel} (${codeToDelete})?`)) {
    return;
  }

  try {
    const seatId = seatObj?.id || codeToDelete;
    let res = await fetch(`${API_BASE}/seats/${encodeURIComponent(seatId)}`, {
      method: "DELETE",
    });

    if (!res.ok) {
      // Fallback by code
      res = await fetch(`${API_BASE}/seats/by-code/${encodeURIComponent(codeToDelete)}`, {
        method: "DELETE",
      });
    }

    if (res.ok || res.status === 204) {
      showToast(`Deleted Seat ROI: ${seatLabel}`, "success");
    } else {
      showToast(`Deleted Seat ROI locally: ${seatLabel}`, "info");
    }

    // Remove from local active seats
    availableSeats = availableSeats.filter((s) => s.seat_code !== codeToDelete);
    if (selectedSeatCode === codeToDelete) {
      selectedSeatCode = availableSeats.length > 0 ? availableSeats[0].seat_code : null;
    }
    if (hoveredSeatCode === codeToDelete) {
      hoveredSeatCode = null;
    }

    populateSeatDropdowns();
    updateDeleteSeatButtonVisibility();
    renderVideoOverlay();
  } catch (err) {
    showToast(`Failed to delete seat: ${err.message}`, "error");
  }
}

function initVideoHotkeys() {
  window.addEventListener("keydown", (e) => {
    if (currentTab !== "videos") return;
    if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.tagName === "SELECT") return;

    if (e.key === "Delete" || e.key === "Backspace") {
      if (selectedSeatCode) {
        e.preventDefault();
        deleteSelectedSeat(selectedSeatCode);
      }
    } else if (e.key === "[") {
      markStartTimestamp();
    } else if (e.key === "]") {
      markEndTimestamp();
    } else if (e.key === "p" || e.key === "P") {
      markPeakTimestamp();
    }
  });
}

async function loadVideosList() {
  try {
    const res = await fetch(`${API_BASE}/videos`);
    const data = await res.json();
    const select = document.getElementById("video-asset-select");
    if (!select) return;

    select.innerHTML = (data.items || [])
      .map((v) => `<option value="${v.id}">${v.file_name} (${v.duration_seconds}s - ${v.total_episodes} eps)</option>`)
      .join("");

    if (data.items && data.items.length > 0) {
      selectVideoAsset(data.items[0].id);
    }
  } catch (err) {
    console.error("Error loading videos:", err);
  }
}

async function selectVideoAsset(videoId) {
  try {
    const res = await fetch(`${API_BASE}/videos`);
    const data = await res.json();
    currentVideoAsset = data.items.find((v) => v.id === videoId);

    const videoEl = document.getElementById("workbench-video-player");
    if (videoEl && currentVideoAsset) {
      videoEl.src = `${API_BASE}/videos/${currentVideoAsset.id}/file`;
    }

    loadAvailableSeats();
    loadVideoEpisodes(videoId);
    loadVideoComparison(videoId);
  } catch (err) {
    console.error("Error selecting video:", err);
  }
}

async function loadVideoEpisodes(videoId) {
  try {
    const res = await fetch(`${API_BASE}/videos/${videoId}/episodes`);
    const data = await res.json();
    videoEpisodes = data.episodes || [];
    renderTimelineBlocks();
    renderEpisodesTable();
  } catch (err) {
    console.error("Error loading video episodes:", err);
  }
}

async function loadVideoComparison(videoId) {
  try {
    const res = await fetch(`${API_BASE}/videos/${videoId}/compare?iou_threshold=0.30`);
    const comp = await res.json();

    document.getElementById("comp-precision").innerText = `${(comp.precision * 100).toFixed(1)}%`;
    document.getElementById("comp-recall").innerText = `${(comp.recall * 100).toFixed(1)}%`;
    document.getElementById("comp-f1").innerText = `${(comp.f1_score * 100).toFixed(1)}%`;
    document.getElementById("comp-avg-iou").innerText = comp.average_temporal_iou.toFixed(2);

    const tbody = document.getElementById("comparison-table-body");
    if (tbody && comp.matched_pairs) {
      tbody.innerHTML = comp.matched_pairs
        .map(
          (m) => `
        <tr>
          <td><span class="badge-tag badge-blue">${m.seat_code || 'SEAT'}</span></td>
          <td><span class="badge-tag badge-green">${m.human_type}</span> [${m.human_range_ms[0]} - ${m.human_range_ms[1]}ms]</td>
          <td><span class="badge-tag badge-purple">${m.ai_type}</span> [${m.ai_range_ms[0]} - ${m.ai_range_ms[1]}ms]</td>
          <td><strong>${(m.temporal_iou * 100).toFixed(1)}%</strong></td>
          <td>${m.start_latency_ms > 0 ? '+' : ''}${m.start_latency_ms} ms</td>
          <td><span class="badge-tag ${m.is_label_match ? 'badge-green' : 'badge-amber'}">${m.is_label_match ? 'MATCH' : 'LABEL_DIFF'}</span></td>
        </tr>
      `
        )
        .join("");
    }
  } catch (err) {
    console.error("Error loading comparison:", err);
  }
}

function initTimelineControls() {
  const videoEl = document.getElementById("workbench-video-player");
  const playhead = document.getElementById("timeline-playhead");
  const track = document.getElementById("timeline-track-wrapper");

  if (videoEl && playhead && track) {
    videoEl.addEventListener("timeupdate", () => {
      if (videoEl.duration > 0) {
        const pct = (videoEl.currentTime / videoEl.duration) * 100;
        playhead.style.left = `${pct}%`;
        const timeDisplay = document.getElementById("current-time-ms-display");
        if (timeDisplay) timeDisplay.innerText = `${Math.round(videoEl.currentTime * 1000)} ms`;
      }
    });

    track.addEventListener("click", (e) => {
      const rect = track.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const pct = clickX / rect.width;
      if (videoEl.duration > 0) {
        videoEl.currentTime = pct * videoEl.duration;
      }
    });
  }
}

function renderTimelineBlocks() {
  const track = document.getElementById("timeline-track");
  const videoEl = document.getElementById("workbench-video-player");
  if (!track || !currentVideoAsset) return;

  const durationMs = (currentVideoAsset.duration_seconds || 30.0) * 1000;
  track.innerHTML = "";

  videoEpisodes.forEach((ep) => {
    const leftPct = (ep.start_ms / durationMs) * 100;
    const widthPct = Math.max(1, ((ep.end_ms - ep.start_ms) / durationMs) * 100);

    const block = document.createElement("div");
    block.className = `timeline-block ${ep.is_ai_proposal ? 'ai' : 'human'}`;
    block.style.left = `${leftPct}%`;
    block.style.width = `${widthPct}%`;
    block.title = `${ep.episode_type} (${ep.start_ms} - ${ep.end_ms} ms) [${ep.seat_code || 'SEAT'}]`;
    block.innerText = `${ep.seat_code ? ep.seat_code + ': ' : ''}${ep.episode_type}`;
    block.onclick = (e) => {
      e.stopPropagation();
      if (videoEl) videoEl.currentTime = ep.start_ms / 1000.0;
    };
    track.appendChild(block);
  });
}

function renderEpisodesTable() {
  const tbody = document.getElementById("episodes-table-body");
  if (!tbody) return;

  tbody.innerHTML = videoEpisodes
    .map(
      (ep) => `
    <tr>
      <td><span class="badge-tag ${ep.is_ai_proposal ? 'badge-purple' : 'badge-green'}">${ep.is_ai_proposal ? 'AI PROPOSAL' : 'HUMAN GT'}</span></td>
      <td><strong>${ep.seat_code || 'SEAT'}</strong></td>
      <td><span class="badge-tag badge-blue">${ep.episode_type}</span></td>
      <td>${ep.start_ms} ms</td>
      <td>${ep.end_ms} ms</td>
      <td>${ep.duration_ms} ms</td>
      <td>${ep.target_neighbor_id || '-'}</td>
      <td>
        ${!ep.is_ai_proposal ? `<button class="btn btn-sm btn-danger" onclick="deleteEpisode('${ep.id}')">Delete</button>` : ''}
      </td>
    </tr>
  `
    )
    .join("");
}

function markStartTimestamp() {
  const videoEl = document.getElementById("workbench-video-player");
  if (videoEl) {
    document.getElementById("ep-start-ms").value = Math.round(videoEl.currentTime * 1000);
  }
}

function markPeakTimestamp() {
  const videoEl = document.getElementById("workbench-video-player");
  if (videoEl) {
    document.getElementById("ep-peak-ms").value = Math.round(videoEl.currentTime * 1000);
  }
}

function markEndTimestamp() {
  const videoEl = document.getElementById("workbench-video-player");
  if (videoEl) {
    document.getElementById("ep-end-ms").value = Math.round(videoEl.currentTime * 1000);
  }
}

async function createEpisode() {
  if (!currentVideoAsset) return;

  const epType = document.getElementById("ep-type-select").value;
  const startMs = parseFloat(document.getElementById("ep-start-ms").value || "0");
  const peakMs = parseFloat(document.getElementById("ep-peak-ms").value || startMs);
  const endMs = parseFloat(document.getElementById("ep-end-ms").value || "0");
  const seatCode = document.getElementById("ep-seat-code").value;
  const targetNeighbor = document.getElementById("ep-neighbor-code").value;
  const notes = document.getElementById("ep-notes").value;

  if (endMs <= startMs) {
    showToast("End timestamp must be greater than Start timestamp", "error");
    return;
  }

  const payload = {
    episode_type: epType,
    start_ms: startMs,
    peak_ms: peakMs,
    end_ms: endMs,
    seat_code: seatCode,
    target_neighbor_id: targetNeighbor || null,
    reviewer_id: activeRole,
    notes: notes,
  };

  try {
    const res = await fetch(`${API_BASE}/videos/${currentVideoAsset.id}/episodes`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify(payload),
    });
    if (res.ok) {
      showToast("Ground-truth episode added!", "success");
      loadVideoEpisodes(currentVideoAsset.id);
      loadVideoComparison(currentVideoAsset.id);
    }
  } catch (err) {
    showToast("Failed to create episode", "error");
  }
}

async function deleteEpisode(epId) {
  if (!currentVideoAsset) return;
  try {
    const res = await fetch(`${API_BASE}/videos/${currentVideoAsset.id}/episodes/${epId}`, { method: "DELETE" });
    if (res.ok) {
      showToast("Episode deleted", "info");
      loadVideoEpisodes(currentVideoAsset.id);
      loadVideoComparison(currentVideoAsset.id);
    }
  } catch (err) {
    showToast("Failed to delete episode", "error");
  }
}

// -----------------------------------------------------------------------------
// 4. SPATIAL CALIBRATION & QUALITY VALIDATOR
// -----------------------------------------------------------------------------
async function runCalibrationCheck() {
  const resultsCard = document.getElementById("calibration-check-results");
  if (!resultsCard) return;

  try {
    const res = await fetch(`${API_BASE}/calibration/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify({}),
    });
    const rep = await res.json();

    let html = `
      <div class="stat-card" style="margin-bottom: 1rem; --card-accent: ${rep.valid ? 'var(--accent-green)' : 'var(--accent-rose)'};">
        <div class="stat-title">Validation Status</div>
        <div class="stat-value" style="color: ${rep.valid ? 'var(--accent-green)' : 'var(--accent-rose)'}">
          ${rep.valid ? 'PASSED — ALL SEATS VALID' : 'FAILED — GEOMETRIC ISSUES FOUND'}
        </div>
        <div class="stat-subtitle">${rep.total_seats_evaluated} seats evaluated</div>
      </div>
    `;

    if (rep.errors && rep.errors.length > 0) {
      html += `<div style="background: rgba(244,63,94,0.1); border: 1px solid var(--accent-rose); border-radius: var(--radius-sm); padding: 0.75rem; margin-bottom: 1rem;">
        <h4 style="color: var(--accent-rose); margin-bottom: 0.5rem;">Errors (${rep.errors.length})</h4>
        <ul style="padding-left: 1.25rem;">${rep.errors.map(e => `<li>${e}</li>`).join('')}</ul>
      </div>`;
    }

    if (rep.warnings && rep.warnings.length > 0) {
      html += `<div style="background: rgba(245,158,11,0.1); border: 1px solid var(--accent-amber); border-radius: var(--radius-sm); padding: 0.75rem; margin-bottom: 1rem;">
        <h4 style="color: var(--accent-amber); margin-bottom: 0.5rem;">Warnings (${rep.warnings.length})</h4>
        <ul style="padding-left: 1.25rem;">${rep.warnings.map(w => `<li>${w}</li>`).join('')}</ul>
      </div>`;
    }

    // Seat metrics table
    html += `
      <table class="data-table">
        <thead>
          <tr>
            <th>Seat Code</th>
            <th>Points</th>
            <th>Area</th>
            <th>Convex</th>
            <th>Writing Zone</th>
            <th>Status</th>
          </tr>
        </thead>
        <tbody>
          ${Object.entries(rep.seat_metrics || {}).map(([code, m]) => `
            <tr>
              <td><strong>${code}</strong></td>
              <td>${m.num_points} pts</td>
              <td>${m.area}</td>
              <td><span class="badge-tag ${m.is_convex ? 'badge-green' : 'badge-amber'}">${m.is_convex ? 'YES' : 'NON-CONVEX'}</span></td>
              <td><span class="badge-tag ${m.has_desk_zone ? 'badge-green' : 'badge-blue'}">${m.has_desk_zone ? 'CONFIGURED' : 'DEFAULT'}</span></td>
              <td><span class="badge-tag badge-green">ENABLED</span></td>
            </tr>
          `).join('')}
        </tbody>
      </table>
    `;

    resultsCard.innerHTML = html;
  } catch (err) {
    resultsCard.innerHTML = `<p class="text-danger">Failed to run calibration check: ${err.message}</p>`;
  }
}

// -----------------------------------------------------------------------------
// 5. AI EVENT REVIEW QUEUE
// -----------------------------------------------------------------------------
async function loadEventReviewQueue() {
  const container = document.getElementById("event-review-queue-container");
  if (!container) return;

  try {
    const res = await fetch(`${API_BASE}/events/review-queue?status_filter=PENDING`);
    const data = await res.json();

    if (!data.items || data.items.length === 0) {
      container.innerHTML = `<div class="stat-card" style="text-align: center; padding: 2rem;">
        <h3 style="color: var(--accent-green); margin-bottom: 0.5rem;">Review Queue Empty</h3>
        <p class="text-secondary">All AI suspicious events have been reviewed by proctors.</p>
      </div>`;
      return;
    }

    container.innerHTML = data.items
      .map(
        (ev) => `
      <div class="panel-card" style="margin-bottom: 1rem;">
        <div class="panel-header">
          <div class="panel-title">
            <span class="badge-tag badge-rose">${ev.severity}</span>
            <span>${ev.primary_signal || ev.behavior}</span>
            <span class="badge-tag badge-blue">${ev.seat_code}</span>
          </div>
          <div>Risk Score: <strong>${ev.risk_score} / 100</strong></div>
        </div>
        <div style="display: grid; grid-template-columns: 280px 1fr; gap: 1rem; align-items: center;">
          <div>
            ${ev.evidence_video ? `<video src="/${ev.evidence_video}" controls style="max-width: 100%; border-radius: var(--radius-sm);"></video>` : (ev.evidence_snapshot ? `<img src="/${ev.evidence_snapshot}" style="max-width: 100%; border-radius: var(--radius-sm);" />` : '<div style="background:#000; height:140px; display:flex; align-items:center; justify-content:center;">No Evidence</div>')}
            ${ev.video_sha256 ? `<div style="font-size:0.65rem; color:var(--text-muted); font-family:var(--font-mono); margin-top:4px;">SHA: ${ev.video_sha256.substring(0, 16)}...</div>` : ''}
          </div>
          <div>
            <p style="margin-bottom: 0.5rem;"><strong>Primary Pattern:</strong> ${ev.primary_pattern || 'PROLONGED_HEAD_TURN'}</p>
            <p style="margin-bottom: 0.5rem;"><strong>Duration:</strong> ${ev.duration_seconds.toFixed(1)}s (Peak Conf: ${(ev.confidence_peak * 100).toFixed(1)}%)</p>
            <div style="display: flex; gap: 0.5rem; margin-top: 1rem;">
              <button class="btn btn-success btn-sm" onclick="reviewEvent('${ev.id}', 'CONFIRMED', 'TRUE_SUSPICIOUS')">Confirm Violation</button>
              <button class="btn btn-danger btn-sm" onclick="reviewEvent('${ev.id}', 'REJECTED', 'NORMAL_BEHAVIOR')">Reject (False Alarm)</button>
              <button class="btn btn-warning btn-sm" onclick="reviewEvent('${ev.id}', 'INCONCLUSIVE', 'PROCTOR_OCCLUSION')">Inconclusive</button>
            </div>
          </div>
        </div>
      </div>
    `
      )
      .join("");
  } catch (err) {
    console.error("Error loading event review queue:", err);
  }
}

async function reviewEvent(eventId, decision, reasonCode) {
  const payload = {
    decision: decision,
    reason_code: reasonCode,
    note: `Triage decision made by ${activeRole}`,
    reviewer_id: activeRole,
  };

  try {
    const res = await fetch(`${API_BASE}/events/${eventId}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify(payload),
    });
    if (res.ok) {
      showToast(`Event marked as ${decision}`, "success");
      loadEventReviewQueue();
      loadSummary();
    }
  } catch (err) {
    showToast("Failed to submit event review", "error");
  }
}

// -----------------------------------------------------------------------------
// 6. DATASET CURATION & EXPORT
// -----------------------------------------------------------------------------
async function loadDatasetsList() {
  const container = document.getElementById("dataset-versions-container");
  if (!container) return;

  try {
    const res = await fetch(`${API_BASE}/datasets`);
    const data = await res.json();

    if (!data.collections || data.collections.length === 0) {
      container.innerHTML = "<p class='text-secondary'>No exported datasets yet. Click 'Export New Version' above.</p>";
      return;
    }

    container.innerHTML = data.collections
      .map(
        (col) => `
      <div class="panel-card" style="margin-bottom: 1.5rem;">
        <div class="panel-header">
          <div class="panel-title">${col.name} <span class="badge-tag badge-blue">${col.task_type}</span></div>
          <div>${col.versions_count} versions</div>
        </div>
        <p class="text-secondary" style="font-size:0.85rem; margin-bottom:1rem;">${col.description || ''}</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>Version Tag</th>
              <th>Format</th>
              <th>Total Samples</th>
              <th>Split Strategy</th>
              <th>Status</th>
              <th>Manifest</th>
            </tr>
          </thead>
          <tbody>
            ${col.versions.map(v => `
              <tr>
                <td><strong>${v.version_tag}</strong></td>
                <td><span class="badge-tag badge-purple">${v.export_format}</span></td>
                <td>${v.total_items} items</td>
                <td>${v.split_strategy}</td>
                <td><span class="badge-tag badge-green">${v.status}</span></td>
                <td>
                  <button class="btn btn-sm btn-primary" onclick="viewManifestModal('${encodeURIComponent(JSON.stringify(v.manifest))}')">View Manifest</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `
      )
      .join("");
  } catch (err) {
    console.error("Error loading datasets:", err);
  }
}

async function triggerDatasetExport() {
  const colName = document.getElementById("export-collection-name").value || "VIGIL_707_ACTOR_CROPS";
  const verTag = document.getElementById("export-version-tag").value || "v1.0.0";
  const format = document.getElementById("export-format-select").value || "CLASSIFICATION_CROPS";
  const trainRatio = parseFloat(document.getElementById("export-train-ratio").value || "0.70");
  const valRatio = parseFloat(document.getElementById("export-val-ratio").value || "0.15");
  const testRatio = parseFloat(document.getElementById("export-test-ratio").value || "0.15");

  showToast(`Starting export of ${colName} (${verTag})...`, "info");

  const payload = {
    collection_name: colName,
    version_tag: verTag,
    task_type: format === "EPISODE_JSON" ? "TEMPORAL_EPISODE" : "ACTOR_CLASSIFICATION",
    export_format: format,
    train_ratio: trainRatio,
    val_ratio: valRatio,
    test_ratio: testRatio,
    target_crop_size: 224,
  };

  try {
    const res = await fetch(`${API_BASE}/datasets/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify(payload),
    });
    if (res.ok) {
      const data = await res.json();
      showToast(`Exported ${data.total_items} items to ${data.export_path}!`, "success");
      loadDatasetsList();
    } else {
      const err = await res.json();
      showToast(`Export failed: ${err.detail}`, "error");
    }
  } catch (err) {
    showToast("Export request failed", "error");
  }
}

function viewManifestModal(manifestStr) {
  try {
    const manifest = JSON.parse(decodeURIComponent(manifestStr));
    alert("DATASET MANIFEST JSON:\n\n" + JSON.stringify(manifest, null, 2));
  } catch (e) {
    alert("Manifest: " + manifestStr);
  }
}

// -----------------------------------------------------------------------------
// 7. STAGED SESSIONS
// -----------------------------------------------------------------------------
async function loadStagedSessions() {
  const container = document.getElementById("staged-sessions-container");
  if (!container) return;

  try {
    const res = await fetch(`${API_BASE}/staged-sessions`);
    const data = await res.json();

    container.innerHTML = (data.sessions || [])
      .map(
        (s) => `
      <div class="panel-card" style="margin-bottom: 1.5rem;">
        <div class="panel-header">
          <div class="panel-title">${s.session_code} — ${s.script_name}</div>
          <span class="badge-tag badge-blue">${s.status}</span>
        </div>
        <p class="text-secondary" style="font-size:0.85rem; margin-bottom:1rem;"><strong>Actors:</strong> ${s.actors.join(', ')}</p>
        <table class="data-table">
          <thead>
            <tr>
              <th>Scenario Code</th>
              <th>Title</th>
              <th>Expected Action</th>
              <th>Target Window</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            ${s.scenarios.map(sc => `
              <tr>
                <td><strong>${sc.scenario_code}</strong></td>
                <td>${sc.title}</td>
                <td><span class="badge-tag badge-purple">${sc.expected_behavior}</span></td>
                <td>${sc.target_start_ms} - ${sc.target_end_ms} ms</td>
                <td><span class="badge-tag ${sc.status === 'PASS' ? 'badge-green' : (sc.status === 'FAIL' ? 'badge-rose' : 'badge-amber')}">${sc.status}</span></td>
                <td>
                  <button class="btn btn-sm btn-success" onclick="updateScenarioStatus('${s.id}', '${sc.id}', 'PASS')">Pass</button>
                  <button class="btn btn-sm btn-danger" onclick="updateScenarioStatus('${s.id}', '${sc.id}', 'FAIL')">Fail</button>
                </td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      </div>
    `
      )
      .join("");
  } catch (err) {
    console.error("Error loading staged sessions:", err);
  }
}

async function updateScenarioStatus(sessionId, checklistId, status) {
  try {
    const res = await fetch(`${API_BASE}/staged-sessions/${sessionId}/scenarios/${checklistId}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: jsonSafeStringify({ status: status }),
    });
    if (res.ok) {
      showToast(`Scenario marked as ${status}`, "success");
      loadStagedSessions();
    }
  } catch (err) {
    showToast("Failed to update scenario", "error");
  }
}

// Utility
function jsonSafeStringify(obj) {
  return JSON.stringify(obj);
}
