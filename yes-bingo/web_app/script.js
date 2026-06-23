'use strict';

// ── Telegram WebApp ──────────────────────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.enableClosingConfirmation(); }

// ── URL params ───────────────────────────────────────────────────────────────
const params  = new URLSearchParams(window.location.search);
const GAME_ID = params.get('game_id') || '';
const USER_ID = params.get('user_id') || '';

// ── State ────────────────────────────────────────────────────────────────────
const S = {
  user:          null,
  game:          null,
  player:        null,
  boards:        [],        // [{flat, boardNumber, marked:Set}]
  activeBoardIdx:0,
  calledNumbers: new Set(),
  currentCall:   null,
  gameOver:      false,
  hasBingo:      false,
  wsConnected:   false,
  pollInterval:  null,
  countdownTimer:null,
  wsRetries:     0,
  ws:            null,
};

// ── DOM helper ───────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ── Toast ────────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, ms = 2500) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

// ── Tab navigation ───────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  $('tab-' + name).classList.add('active');
  $('nav-' + name).classList.add('active');
  if (name === 'scores')  loadLeaderboard();
  if (name === 'history') loadHistory();
  if (name === 'profile') loadProfile();
  if (name === 'wallet')  refreshWallet();
}

// ── API fetch ────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  try {
    const r = await fetch(path, {
      headers: { 'Content-Type': 'application/json', 'X-User-Id': USER_ID },
      ...opts,
    });
    if (!r.ok) return null;
    return await r.json();
  } catch { return null; }
}

// ══════════════════════════════════════════════════════════════════════════════
// WebSocket
// ══════════════════════════════════════════════════════════════════════════════
function buildWsUrl() {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws?game_id=${encodeURIComponent(GAME_ID)}&user_id=${encodeURIComponent(USER_ID)}`;
}

function connectWS() {
  if (!GAME_ID || S.gameOver) return;
  try {
    S.ws = new WebSocket(buildWsUrl());
  } catch {
    fallbackToPolling();
    return;
  }

  S.ws.onopen = () => {
    S.wsConnected = true;
    S.wsRetries   = 0;
    stopPolling();
    showToast('🟢 Live connected');
    updateLiveDot(true);
  };

  S.ws.onmessage = e => {
    try { handleWsMsg(JSON.parse(e.data)); } catch { /* ignore */ }
  };

  S.ws.onerror = () => {
    S.wsConnected = false;
    updateLiveDot(false);
    fallbackToPolling();
  };

  S.ws.onclose = () => {
    S.wsConnected = false;
    updateLiveDot(false);
    if (!S.gameOver) {
      S.wsRetries++;
      const delay = Math.min(500 * S.wsRetries, 8000);
      setTimeout(connectWS, delay);
    }
  };
}

function updateLiveDot(live) {
  const dot  = document.querySelector('.live-dot');
  const text = document.querySelector('.live-text');
  if (!dot || !text) return;
  if (live) {
    dot.style.background  = 'var(--accent-green)';
    text.style.color      = 'var(--accent-green)';
    text.textContent      = 'Live';
  } else {
    dot.style.background  = '#555';
    text.style.color      = '#555';
    text.textContent      = 'Connecting…';
  }
}

// ── WebSocket message dispatcher ─────────────────────────────────────────────
function handleWsMsg(data) {
  switch (data.type) {

    case 'init': {
      // Full snapshot sent on first connect
      if (data.game)   applyGameSnapshot(data.game);
      if (data.player && S.boards.length === 0) buildBoards(data.player);
      break;
    }

    case 'countdown': {
      const cdEl = $('countdown');
      cdEl.className   = 'countdown waiting';
      cdEl.textContent = data.seconds + 's';
      break;
    }

    case 'game_start': {
      const cdEl = $('countdown');
      cdEl.className   = 'countdown';
      cdEl.textContent = '75 left';
      $('statPlayers').textContent = data.player_count + '/100';
      $('statPot').textContent     = fmtEtb(data.total_pot);
      $('statPrize').textContent   = fmtEtb(data.total_pot * 0.8);
      showToast('🎮 Game Started! Good luck! 🍀', 3000);
      break;
    }

    case 'number_called': {
      const n = data.number;
      S.calledNumbers.add(n);
      S.currentCall = n;

      // Flash the called-numbers grid cell
      flashCalledCell(n);

      // Update header
      $('currentCall').textContent          = n;
      $('currentCallLabel').textContent     = '📢 Current Call: ';

      // Update countdown badge
      const cdEl = $('countdown');
      cdEl.className   = 'countdown';
      cdEl.textContent = (data.remaining ?? (75 - data.called.length)) + ' left';

      // Re-render board to highlight available cells
      renderBoard();
      break;
    }

    case 'player_joined': {
      $('statPlayers').textContent = data.player_count + '/100';
      $('statPot').textContent     = fmtEtb(data.total_pot);
      $('statPrize').textContent   = fmtEtb(data.total_pot * 0.8);
      showToast('👤 New player joined!');
      break;
    }

    case 'player_left': {
      $('statPlayers').textContent = (data.player_count ?? '?') + '/100';
      break;
    }

    case 'player_eliminated': {
      if (String(data.user_id) === String(USER_ID)) {
        S.gameOver = true;
        stopPolling();
        showToast('❌ You have been eliminated.', 4000);
        $('bingoBtn').textContent = '❌ Eliminated';
        $('bingoBtn').disabled    = true;
        $('bingoBtn').classList.remove('active');
      }
      break;
    }

    case 'board_added': {
      // Extra board purchased
      S.boards.push({ flat: data.flat, boardNumber: data.board_number, marked: new Set() });
      buildBoardTabs();
      showToast(`📋 Extra board #${data.board_number} added!`);
      break;
    }

    case 'game_end': {
      S.gameOver = true;
      stopPolling();
      const isWinner = String(data.winner_id) === String(USER_ID);
      if (isWinner) {
        showWinModal(data.winner_name, data.board_number, data.amount);
      } else {
        showToast(`🎉 ${data.winner_name} won ${fmtEtb(data.amount)}!`, 5000);
        const cdEl = $('countdown');
        cdEl.className   = 'countdown finished';
        cdEl.textContent = 'Finished';
        $('bingoBtn').disabled = true;
      }
      break;
    }

    case 'game_no_winner': {
      S.gameOver = true;
      stopPolling();
      showToast('🎮 Game over — no winner. Stake refunded.', 4000);
      const cdEl = $('countdown');
      cdEl.className   = 'countdown finished';
      cdEl.textContent = 'Refunded';
      break;
    }

    case 'game_cancelled': {
      S.gameOver = true;
      stopPolling();
      showToast('⚠️ Game cancelled — not enough players. Refund issued.', 4000);
      break;
    }

    case 'pong': break;
  }
}

// ── Polling fallback ──────────────────────────────────────────────────────────
function fallbackToPolling() {
  if (S.pollInterval || S.gameOver) return;
  S.pollInterval = setInterval(pollGameState, 3000);
}

function stopPolling() {
  clearInterval(S.pollInterval);
  S.pollInterval = null;
}

async function pollGameState() {
  if (S.gameOver || !GAME_ID) return;
  const data = await api(`/api/game/state?game_id=${GAME_ID}&user_id=${USER_ID}`);
  if (!data?.game) return;
  applyGameSnapshot(data.game);
  if (data.player && S.boards.length === 0) buildBoards(data.player);
  if (data.game.status === 'finished' && data.winner_id) {
    showWinModal(data.winner_name || '?', data.winner_board || '-', data.winner_amount || 0);
  }
}

// ─────────────────────────────────────────────────────────────────────────────
// Game state helpers
// ─────────────────────────────────────────────────────────────────────────────
function applyGameSnapshot(game) {
  S.game = game;

  // Sync called numbers
  const arr = game.called_numbers || [];
  const prev = S.calledNumbers.size;
  arr.forEach(n => S.calledNumbers.add(n));
  if (S.calledNumbers.size > prev) {
    rebuildCalledGrid();
    const last = arr[arr.length - 1];
    if (last) {
      S.currentCall = last;
      $('currentCall').textContent = last;
    }
  }

  // Update info panels
  $('gameId').textContent      = game.game_id || GAME_ID;
  $('statPlayers').textContent = game.player_count + '/100';
  $('statPot').textContent     = fmtEtb(game.total_pot);
  $('statPrize').textContent   = fmtEtb(game.total_pot * 0.8);

  const cdEl = $('countdown');
  if (game.status === 'waiting') {
    cdEl.className   = 'countdown waiting';
    cdEl.textContent = 'Waiting';
  } else if (game.status === 'active') {
    cdEl.className   = 'countdown';
    cdEl.textContent = (75 - arr.length) + ' left';
  } else {
    cdEl.className   = 'countdown finished';
    cdEl.textContent = 'Finished';
    S.gameOver = true;
  }
  renderBoard();
}

// ─────────────────────────────────────────────────────────────────────────────
// Called Numbers Grid (1-75)
// ─────────────────────────────────────────────────────────────────────────────
function buildCalledGrid() {
  const grid = $('calledGrid');
  grid.innerHTML = '';
  for (let i = 1; i <= 75; i++) {
    const cell = document.createElement('div');
    cell.className   = 'num-cell' + (S.calledNumbers.has(i) ? ' called' : '');
    cell.textContent = i;
    cell.id          = 'cn-' + i;
    grid.appendChild(cell);
  }
}

function rebuildCalledGrid() {
  // Just toggle classes — don't recreate DOM
  for (let i = 1; i <= 75; i++) {
    const el = $('cn-' + i);
    if (el) el.className = 'num-cell' + (S.calledNumbers.has(i) ? ' called' : '');
  }
}

function flashCalledCell(n) {
  const el = $('cn-' + n);
  if (!el) return;
  el.className = 'num-cell called';
}

// ─────────────────────────────────────────────────────────────────────────────
// Bingo Board
// ─────────────────────────────────────────────────────────────────────────────
function buildBoards(player) {
  S.boards = [];
  if (player.main_board) {
    S.boards.push({
      flat: player.main_board,
      boardNumber: player.board_numbers?.[0] ?? 0,
      marked: new Set(),
    });
  }
  (player.extra_boards || []).forEach((eb, i) => {
    S.boards.push({
      flat: eb,
      boardNumber: player.board_numbers?.[i + 1] ?? (100 + i + 1),
      marked: new Set(),
    });
  });
  buildBoardTabs();
  renderBoard();
}

function buildBoardTabs() {
  const sel = $('boardSelector');
  sel.innerHTML = '';
  S.boards.forEach((b, i) => {
    const btn       = document.createElement('button');
    btn.className   = 'board-tab' + (i === S.activeBoardIdx ? ' active' : '');
    btn.textContent = i === 0 ? `Board #${b.boardNumber}` : `Extra #${b.boardNumber}`;
    btn.onclick     = () => { S.activeBoardIdx = i; renderBoard(); buildBoardTabs(); };
    sel.appendChild(btn);
  });
}

function renderBoard() {
  const grid  = $('bingoGrid');
  const board = S.boards[S.activeBoardIdx];
  if (!board) { grid.innerHTML = ''; return; }

  grid.innerHTML = '';
  // flat is column-major: flat[col*5 + row]
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      const idx      = col * 5 + row;
      const val      = board.flat[idx];
      const isCenter = row === 2 && col === 2;
      const cell     = document.createElement('div');

      if (isCenter || val === null) {
        cell.className   = 'bingo-cell free';
        cell.textContent = '★';
      } else {
        const isCalled = S.calledNumbers.has(val);
        const isMarked = board.marked.has(idx);
        cell.className   = 'bingo-cell';
        if (isMarked)       cell.classList.add('marked');
        else if (isCalled)  cell.classList.add('called-available');
        cell.textContent = val;
        cell.onclick     = () => tapCell(idx, val);
      }
      grid.appendChild(cell);
    }
  }

  $('statBoard').textContent = board.boardNumber;
  checkBingo();
}

function tapCell(idx, val) {
  if (S.gameOver) return;
  const board = S.boards[S.activeBoardIdx];
  if (!board) return;
  if (!S.calledNumbers.has(val)) {
    showToast(`${val} hasn't been called yet!`);
    return;
  }
  if (board.marked.has(idx)) board.marked.delete(idx);
  else                        board.marked.add(idx);
  renderBoard();
}

// ─────────────────────────────────────────────────────────────────────────────
// BINGO detection
// ─────────────────────────────────────────────────────────────────────────────
function checkBingo() {
  if (S.gameOver) return;
  const won = S.boards.some(boardHasBingo);
  S.hasBingo = won;
  const btn  = $('bingoBtn');
  if (btn.textContent === '❌ Eliminated') return;
  btn.disabled = !won;
  if (won) btn.classList.add('active');
  else     btn.classList.remove('active');
}

function boardHasBingo(board) {
  const ok = idx => {
    const val      = board.flat[idx];
    const isCenter = idx === 12;  // col2*5+row2
    return isCenter || val === null || board.marked.has(idx);
  };
  for (let r = 0; r < 5; r++)
    if ([0,1,2,3,4].every(c => ok(c * 5 + r))) return true;
  for (let c = 0; c < 5; c++)
    if ([0,1,2,3,4].every(r => ok(c * 5 + r))) return true;
  if ([0,1,2,3,4].every(i => ok(i * 5 + i)))     return true;
  if ([0,1,2,3,4].every(i => ok((4 - i) * 5 + i))) return true;
  return false;
}

// ─────────────────────────────────────────────────────────────────────────────
// Actions
// ─────────────────────────────────────────────────────────────────────────────
async function claimBingo() {
  if (!S.hasBingo || S.gameOver) return;
  const btn       = $('bingoBtn');
  btn.disabled    = true;
  btn.textContent = '⏳ Checking…';
  btn.classList.remove('active');

  const res = await api('/api/game/claim-bingo', {
    method: 'POST',
    body:   JSON.stringify({ game_id: GAME_ID, user_id: USER_ID }),
  });

  if (res?.success) {
    showWinModal(res.winner_name, res.board_number, res.amount);
  } else if (res?.eliminated) {
    showToast('❌ False BINGO — you have been eliminated!', 4000);
    S.gameOver       = true;
    btn.textContent  = '❌ Eliminated';
  } else if (res) {
    showToast(res.message || 'Could not claim BINGO.', 3000);
    btn.disabled     = false;
    btn.textContent  = '🔴 BINGO!';
    if (S.hasBingo) btn.classList.add('active');
  } else {
    // No API reachable — send via Telegram data channel
    if (tg) tg.sendData(JSON.stringify({ action: 'claim_bingo', game_id: GAME_ID, user_id: USER_ID }));
    showToast('🎯 BINGO claim sent to bot!', 2000);
  }
}

async function refreshGame() {
  await pollGameState();
  showToast('🔄 Refreshed');
}

function leaveGame() {
  if (tg) {
    tg.showConfirm(
      'Leave the game?\n\nRefund only available before the game starts.',
      (ok) => {
        if (!ok) return;
        if (S.game?.status === 'waiting') {
          if (tg) tg.sendData(JSON.stringify({ action: 'leave_game', game_id: GAME_ID, user_id: USER_ID }));
          showToast('👋 Left game. Refund issued.', 2000);
          setTimeout(() => { if (tg) tg.close(); }, 2000);
        } else {
          showToast('⚠️ No refund after game has started.', 3000);
        }
      }
    );
  } else {
    showToast('Use the Leave button in Telegram chat.', 3000);
  }
}

function addBoard() {
  if (tg) tg.sendData(JSON.stringify({ action: 'add_board', game_id: GAME_ID, user_id: USER_ID }));
  showToast('📋 Open Telegram to confirm extra board (+10 ETB).', 3000);
}

// ─────────────────────────────────────────────────────────────────────────────
// Win Modal
// ─────────────────────────────────────────────────────────────────────────────
function showWinModal(winner, boardNum, amount) {
  S.gameOver = true;
  stopPolling();
  $('winnerName').textContent = winner;
  $('winnerBoard').textContent = boardNum;
  $('winAmount').textContent   = fmtEtb(amount);
  $('winModal').classList.add('show');
}

function closeWinModal() {
  $('winModal').classList.remove('show');
  if (tg) tg.close();
}

// ─────────────────────────────────────────────────────────────────────────────
// Secondary tabs
// ─────────────────────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  const data = await api('/api/leaderboard');
  const list = $('leaderboardList');
  if (!data?.players?.length) {
    list.innerHTML = '<div class="empty-state">No players yet.</div>';
    return;
  }
  const medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  list.innerHTML = data.players.map((p, i) => `
    <div class="list-item">
      <span class="list-rank">${medals[i] || (i + 1)}</span>
      <span class="list-name">${esc(p.first_name)}</span>
      <span class="list-value">🪙 ${p.coin_balance} | 🏆 ${p.total_wins}</span>
    </div>`).join('');
}

async function loadHistory() {
  const data = await api(`/api/history?user_id=${USER_ID}`);
  const list = $('historyList');
  if (!data?.transactions?.length) {
    list.innerHTML = '<div class="empty-state">No transactions yet.</div>';
    return;
  }
  const icons   = {deposit:'💰',withdraw:'💸',win:'🏆',bet:'🎲',refund:'🔄',bonus:'🎁',credit_conversion:'🪙',extra_board:'📋',daily_bonus:'🌟'};
  const sIcons  = {approved:'✅',pending:'⏳',rejected:'❌'};
  list.innerHTML = data.transactions.map(tx => `
    <div class="list-item">
      <span>${icons[tx.type] || '💳'}</span>
      <span class="list-name">
        <span class="tx-type tx-${tx.type}">${tx.type.toUpperCase()}</span>
        <div style="font-size:11px;color:#8b949e;">${(tx.created_at||'').slice(0,16)}</div>
      </span>
      <span class="list-value">${parseFloat(tx.amount).toFixed(2)} ETB ${sIcons[tx.status]||''}</span>
    </div>`).join('');
}

async function loadProfile() {
  const data = await api(`/api/profile?user_id=${USER_ID}`);
  if (!data?.user) return;
  const u = data.user;
  $('profileName').textContent     = esc(u.first_name || 'User');
  $('profileUsername').textContent = u.username ? '@' + u.username : '';
  $('profileWins').textContent     = u.total_wins || 0;
  $('profileGames').textContent    = u.total_games || 0;
  $('profileStreak').textContent   = u.win_streak || 0;
  $('profileDeposits').textContent = fmtEtb(u.total_deposits || 0);
  $('profileWithdrawn').textContent= fmtEtb(u.total_withdrawals || 0);
  $('profileCoins').textContent    = u.coin_balance || 0;
  $('profileCode').textContent     = u.referral_code || '-';
}

async function refreshWallet() {
  const data = await api(`/api/profile?user_id=${USER_ID}`);
  if (!data?.user) return;
  $('walletBalance').textContent = fmtEtb(data.user.wallet_balance || 0);
  $('walletCoins').textContent   = data.user.coin_balance || 0;
}

async function loadHeaderBalance() {
  const data = await api(`/api/profile?user_id=${USER_ID}`);
  if (!data?.user) return;
  S.user = data.user;
  $('headerBalance').textContent = parseFloat(data.user.wallet_balance || 0).toFixed(2);
  $('headerCoins').textContent   = data.user.coin_balance || 0;
}

function openTelegramLink(action) {
  if (tg) tg.sendData(JSON.stringify({ action, user_id: USER_ID }));
  showToast(`Open Telegram to ${action}.`);
}

// ─────────────────────────────────────────────────────────────────────────────
// Demo board (when no game_id)
// ─────────────────────────────────────────────────────────────────────────────
function renderDemo() {
  $('gameId').textContent = 'G' + (3700 + Math.floor(Math.random() * 300));
  const cdEl = $('countdown');
  cdEl.className   = 'countdown waiting';
  cdEl.textContent = '25s';
  $('statPlayers').textContent = '2/100';
  $('statPot').textContent     = '20.00 ETB';
  $('statPrize').textContent   = '16.00 ETB';

  const flat = genDemoFlat();
  S.boards   = [{ flat, boardNumber: 37, marked: new Set() }];
  buildBoardTabs();
  buildCalledGrid();
  renderBoard();
}

function genDemoFlat() {
  const ranges = [[1,15],[16,30],[31,45],[46,60],[61,75]];
  const flat   = [];
  ranges.forEach(([lo,hi]) => {
    const pool = Array.from({ length: hi - lo + 1 }, (_, i) => i + lo);
    shuffle(pool);
    for (let r = 0; r < 5; r++) flat.push(pool[r]);
  });
  flat[12] = null;
  return flat;
}

// ─────────────────────────────────────────────────────────────────────────────
// Utils
// ─────────────────────────────────────────────────────────────────────────────
function fmtEtb(v) { return parseFloat(v || 0).toFixed(2) + ' ETB'; }
function esc(s)    { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }
function shuffle(a){ for(let i=a.length-1;i>0;i--){ const j=Math.floor(Math.random()*(i+1));[a[i],a[j]]=[a[j],a[i]]; } return a; }

// ─────────────────────────────────────────────────────────────────────────────
// Heartbeat to keep WebSocket alive
// ─────────────────────────────────────────────────────────────────────────────
setInterval(() => {
  if (S.ws?.readyState === WebSocket.OPEN) {
    S.ws.send(JSON.stringify({ type: 'ping' }));
  }
}, 25000);

// ─────────────────────────────────────────────────────────────────────────────
// Init
// ─────────────────────────────────────────────────────────────────────────────
async function init() {
  const overlay = $('loadingOverlay');

  // Apply Telegram colour scheme
  if (tg?.colorScheme === 'light') {
    const r = document.documentElement.style;
    r.setProperty('--bg-primary',   '#f5f5f5');
    r.setProperty('--bg-secondary', '#ffffff');
    r.setProperty('--bg-card',      '#f0f0f0');
    r.setProperty('--text-primary', '#111');
    r.setProperty('--text-secondary','#555');
    r.setProperty('--border',       '#ddd');
  }

  buildCalledGrid();

  if (GAME_ID) {
    // Try to get initial snapshot via REST first (fast), then connect WS
    const snap = await api(`/api/game/state?game_id=${GAME_ID}&user_id=${USER_ID}`);
    if (snap?.game)   applyGameSnapshot(snap.game);
    if (snap?.player) buildBoards(snap.player);

    connectWS();       // upgrades to live after REST snapshot
  } else {
    renderDemo();      // no game_id → show demo board
  }

  await loadHeaderBalance();

  overlay.classList.add('hidden');
  setTimeout(() => overlay.style.display = 'none', 500);
}

document.addEventListener('DOMContentLoaded', init);
