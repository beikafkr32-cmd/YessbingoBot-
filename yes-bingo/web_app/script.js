'use strict';

// ── Telegram WebApp ──────────────────────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) { tg.ready(); tg.expand(); tg.enableClosingConfirmation(); }

// ── URL params ───────────────────────────────────────────────────────────────
const params      = new URLSearchParams(window.location.search);
const GAME_ID     = params.get('game_id') || '';
const USER_ID     = params.get('user_id') || '';
const FOCUS_STAKE = parseFloat(params.get('stake') || '0');

// ── State ────────────────────────────────────────────────────────────────────
const S = {
  user: null, game: null, player: null, isAdmin: false,
  boards: [], activeBoardIdx: 0,
  calledNumbers: new Set(), currentCall: null,
  gameOver: false, hasBingo: false,
  view: GAME_ID ? 'board' : 'lobby',
  ws: null, wsRetries: 0, wsConnected: false,
  pollInterval: null, lobbyInterval: null,
  // Board selection
  pendingStake: 0, pendingBoardNum: 0,
};

const $ = id => document.getElementById(id);

// ── Toast ─────────────────────────────────────────────────────────────────────
let _toastTimer = null;
function showToast(msg, ms = 2500) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(_toastTimer);
  _toastTimer = setTimeout(() => el.classList.remove('show'), ms);
}

// ── Tab navigation ────────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => {
    p.classList.remove('active');
    if (p.id !== 'tab-admin') p.style.display = '';
  });
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  const panel = $('tab-' + name), nav = $('nav-' + name);
  if (panel) { panel.classList.add('active'); }
  if (nav)   nav.classList.add('active');
  if (name === 'scores')  loadLeaderboard();
  if (name === 'history') loadHistory();
  if (name === 'profile') loadProfile();
  if (name === 'wallet')  refreshWallet();
  if (name === 'admin')   loadAdminDashboard();
}

// ── Lobby / Board views ───────────────────────────────────────────────────────
function showLobby() {
  $('view-lobby').style.display = '';
  $('view-board').style.display = 'none';
  S.view = 'lobby';
  loadLobby();
  startLobbyPoll();
}

function showBoard() {
  $('view-lobby').style.display = 'none';
  $('view-board').style.display = '';
  S.view = 'board';
  stopLobbyPoll();
  buildCalledGrid();   // build 1-75 grid AFTER S.calledNumbers is populated
  buildBoardTabs();
  renderBoard();
}

function backToLobby() {
  if (!S.gameOver && S.game?.status === 'active') {
    showToast('Game is active — use Leave to exit first.', 3000);
    return;
  }
  S.game = null; S.player = null; S.boards = [];
  S.calledNumbers = new Set(); S.currentCall = null;
  S.gameOver = false; S.hasBingo = false;
  if (S.ws) { S.ws.close(); S.ws = null; }
  stopPolling();
  showLobby();
}

// ── API fetch ─────────────────────────────────────────────────────────────────
async function api(path, opts = {}) {
  try {
    const r = await fetch(path, {
      headers: { 'Content-Type': 'application/json', 'X-User-Id': USER_ID },
      ...opts,
    });
    if (!r.ok) { const e = await r.json().catch(() => ({})); return { _err: r.status, ...e }; }
    return await r.json();
  } catch { return null; }
}

// ══════════════════════════════════════════════════════════════════════════════
// BOARD SELECTION MODAL
// ══════════════════════════════════════════════════════════════════════════════

function openBoardSelect(stake) {
  if (!USER_ID) { showToast('Please open from the Telegram bot.', 3000); return; }
  S.pendingStake    = stake;
  S.pendingBoardNum = 0;

  const label = stake > 0 ? `Stake: ${stake} ETB` : 'FREE DEMO (costs coins)';
  $('boardSelectStakeLabel').textContent = label;
  $('selectedBoardNum').textContent = '—';
  $('boardConfirmBtn').disabled = true;

  // Build 1-100 grid
  const grid = $('boardPickGrid');
  grid.innerHTML = '';
  for (let n = 1; n <= 100; n++) {
    const cell = document.createElement('div');
    cell.className   = 'board-pick-cell';
    cell.textContent = n;
    cell.dataset.num = n;
    cell.onclick = () => selectBoardNum(n);
    grid.appendChild(cell);
  }

  $('boardSelectModal').classList.add('show');
}

function selectBoardNum(n) {
  S.pendingBoardNum = n;
  document.querySelectorAll('.board-pick-cell').forEach(c => {
    c.classList.toggle('selected', parseInt(c.dataset.num) === n);
  });
  $('selectedBoardNum').textContent = n;
  $('boardConfirmBtn').disabled = false;
}

function closeBoardSelect() {
  $('boardSelectModal').classList.remove('show');
  S.pendingStake    = 0;
  S.pendingBoardNum = 0;
}

async function confirmBoardSelect() {
  const stake      = S.pendingStake;
  const boardNum   = S.pendingBoardNum;
  if (!boardNum) { showToast('Pick a board number first!'); return; }

  const btn = $('boardConfirmBtn');
  btn.disabled = true; btn.textContent = '⏳ Joining...';

  const data = await api('/api/game/join', {
    method: 'POST',
    body: JSON.stringify({ user_id: USER_ID, stake, board_number: boardNum }),
  });

  btn.disabled = false; btn.textContent = '▶ Start Game';

  if (!data) { showToast('Server error — try again.', 3000); return; }
  if (data.error) {
    showToast(data.message || data.error, 3500);
    return;
  }

  closeBoardSelect();

  // Reset state
  S.gameOver = false; S.hasBingo = false;
  S.boards = []; S.calledNumbers = new Set(); S.currentCall = null;

  if (data.game)   applyGameSnapshot(data.game);
  if (data.player) buildBoards(data.player);

  showBoard();
  connectWS(data.game_id);

  showToast(data.rejoined ? '✅ Rejoined your active game!' : '✅ Joined! Waiting for players...', 3000);
}

// ══════════════════════════════════════════════════════════════════════════════
// LOBBY
// ══════════════════════════════════════════════════════════════════════════════
async function loadLobby() {
  const data = await api('/api/lobby');
  if (!data?.stakes) return;
  renderLobbyRows(data);
}
function startLobbyPoll() { stopLobbyPoll(); S.lobbyInterval = setInterval(loadLobby, 6000); }
function stopLobbyPoll()  { clearInterval(S.lobbyInterval); S.lobbyInterval = null; }

function renderLobbyRows(data) {
  const container = $('lobbyRows');
  container.innerHTML = '';
  data.stakes.forEach(s => {
    const jpPct  = s.jackpot_max > 0 ? Math.min(100, (s.jackpot / s.jackpot_max) * 100) : 0;
    const prize  = s.prize > 0 ? s.prize.toFixed(0) : (s.stake * 0.8 * 2).toFixed(0);
    const players = s.total_players;
    let statusHtml = s.active_count > 0
      ? `<span class="status-tag status-starts">▶ Active</span>`
      : `<span class="status-tag status-ready">READY</span>`;

    const row = document.createElement('div');
    row.className = 'lobby-row' + (FOCUS_STAKE === s.stake ? ' focus-stake' : '');
    row.innerHTML = `
      <div class="lobby-row-main">
        <div class="lrow-bet"><div class="lrow-stake">${s.stake}</div><div class="lrow-unit">ETB</div></div>
        <div class="lrow-win">
          <div class="lrow-prize">🏆 ${prize}</div>
          <div class="lrow-sub">${players} player${players !== 1 ? 's' : ''}</div>
        </div>
        <div class="lrow-right">
          <div class="lrow-status-row">
            <span class="active-badge">ACTIVE ${s.active_count}</span>
            ${statusHtml}
          </div>
          <div class="lrow-btns">
            <button class="lrow-btn bonus-btn" onclick="showToast('🎁 Invite friends to earn bonus coins!',3000)">🎁 BONUS</button>
            <button class="lrow-btn join-btn" onclick="openBoardSelect(${s.stake})">JOIN</button>
          </div>
        </div>
      </div>
      <div class="jackpot-bar-wrap">
        <span class="jackpot-label">JACKPOT</span>
        <div class="jackpot-track"><div class="jackpot-fill" style="width:${jpPct}%"></div></div>
        <span class="jackpot-val">${s.jackpot.toFixed(0)}/${s.jackpot_max}</span>
      </div>`;
    container.appendChild(row);
  });

  if (FOCUS_STAKE > 0) {
    const focused = container.querySelector('.focus-stake');
    if (focused) setTimeout(() => focused.scrollIntoView({ behavior: 'smooth', block: 'center' }), 200);
  }
}

// ══════════════════════════════════════════════════════════════════════════════
// WebSocket
// ══════════════════════════════════════════════════════════════════════════════
function buildWsUrl(gameId) {
  const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
  return `${proto}//${location.host}/ws?game_id=${encodeURIComponent(gameId)}&user_id=${encodeURIComponent(USER_ID)}`;
}

function connectWS(gameId) {
  if (!gameId) return;
  if (S.ws?.readyState === WebSocket.OPEN || S.ws?.readyState === WebSocket.CONNECTING) return;
  try { S.ws = new WebSocket(buildWsUrl(gameId)); } catch { fallbackPoll(gameId); return; }
  S.ws.onopen    = () => { S.wsConnected = true; S.wsRetries = 0; stopPolling(); setLiveDot(true); };
  S.ws.onmessage = e => { try { handleWsMsg(JSON.parse(e.data)); } catch {} };
  S.ws.onerror   = () => { S.wsConnected = false; setLiveDot(false); fallbackPoll(gameId); };
  S.ws.onclose   = () => {
    S.wsConnected = false; setLiveDot(false);
    if (!S.gameOver && S.view === 'board') {
      S.wsRetries++;
      setTimeout(() => connectWS(gameId), Math.min(600 * S.wsRetries, 10000));
    }
  };
}

function setLiveDot(live) {
  const dot = document.querySelector('.live-dot'), txt = $('liveText');
  if (dot) dot.style.background = live ? 'var(--accent-green)' : '#555';
  if (txt) { txt.style.color = live ? 'var(--accent-green)' : '#555'; txt.textContent = live ? 'Live' : '...'; }
}

function handleWsMsg(data) {
  switch (data.type) {
    case 'init':
      if (data.game)   applyGameSnapshot(data.game);
      if (data.player && S.boards.length === 0) buildBoards(data.player);
      break;

    case 'countdown':
      $('countdown').className   = 'countdown waiting';
      $('countdown').textContent = data.seconds + 's';
      break;

    case 'game_start':
      $('countdown').className   = 'countdown';
      $('countdown').textContent = '75 left';
      $('statPlayers').textContent = data.player_count;
      $('statPot').textContent     = fmt(data.total_pot);
      $('statPrize').textContent   = fmt(data.total_pot * 0.8);
      showToast('🎮 Game Started! Good luck! 🍀', 3000);
      break;

    case 'number_called': {
      const n = data.number;
      S.calledNumbers.add(n); S.currentCall = n;
      highlightCalledCell(n);
      $('currentCall').textContent = n;
      $('calledCount').textContent = (data.called?.length ?? S.calledNumbers.size) + '/75';
      $('countdown').className   = 'countdown';
      $('countdown').textContent = (data.remaining ?? (75 - S.calledNumbers.size)) + ' left';
      renderBoard();
      break;
    }

    case 'player_joined':
      $('statPlayers').textContent = data.player_count;
      $('statPot').textContent     = fmt(data.total_pot);
      $('statPrize').textContent   = fmt(data.total_pot * 0.8);
      showToast('👤 New player joined!');
      break;

    case 'player_eliminated':
      if (String(data.user_id) === String(USER_ID)) {
        S.gameOver = true; stopPolling();
        showToast('❌ You were eliminated.', 4000);
        const b = $('bingoBtn'); b.textContent = '❌ Eliminated'; b.disabled = true; b.classList.remove('active');
      }
      break;

    case 'board_added':
      if (!data._target_user || String(data._target_user) === String(USER_ID)) {
        S.boards.push({ flat: data.flat, boardNumber: data.board_number, marked: new Set() });
        buildBoardTabs();
        showToast(`📋 Extra board #${data.board_number} added!`);
      }
      break;

    case 'game_end':
      S.gameOver = true; stopPolling();
      if (String(data.winner_id) === String(USER_ID)) {
        showWinModal(data.winner_name, data.board_number, data.amount);
      } else {
        showToast(`🎉 ${data.winner_name} won ${fmt(data.amount)}!`, 5000);
        $('countdown').className = 'countdown finished'; $('countdown').textContent = 'Finished';
        $('bingoBtn').disabled = true;
      }
      break;

    case 'game_no_winner':
      S.gameOver = true; stopPolling();
      showToast('🎮 No winner — stake refunded.', 4000);
      $('countdown').className = 'countdown finished'; $('countdown').textContent = 'Refunded';
      break;

    case 'game_cancelled':
      S.gameOver = true; stopPolling();
      showToast('⚠️ Game cancelled — refund issued.', 4000);
      break;

    case 'pong': break;
  }
}

function fallbackPoll(gameId) { if (!S.pollInterval) S.pollInterval = setInterval(() => pollState(gameId), 3000); }
function stopPolling()        { clearInterval(S.pollInterval); S.pollInterval = null; }

async function pollState(gameId) {
  if (S.gameOver || !gameId) return;
  const data = await api(`/api/game/state?game_id=${gameId}&user_id=${USER_ID}`);
  if (!data?.game) return;
  applyGameSnapshot(data.game);
  if (data.player && S.boards.length === 0) buildBoards(data.player);
  if (data.game.status === 'finished' && data.winner_id)
    showWinModal(data.winner_name || '?', data.winner_board || '-', data.winner_amount || 0);
}

// ── Apply full game snapshot ──────────────────────────────────────────────────
function applyGameSnapshot(game) {
  S.game = game;
  const arr = game.called_numbers || [];
  S.calledNumbers = new Set(arr);
  if (arr.length) { S.currentCall = arr[arr.length - 1]; $('currentCall').textContent = S.currentCall; }
  $('calledCount').textContent = arr.length + '/75';
  $('gameId').textContent      = game.game_id || GAME_ID;
  $('statPlayers').textContent = game.player_count;
  $('statPot').textContent     = fmt(game.total_pot);
  $('statPrize').textContent   = fmt(game.total_pot * 0.8);

  const cd = $('countdown');
  if (game.status === 'waiting') {
    cd.className = 'countdown waiting'; cd.textContent = 'Waiting…';
  } else if (game.status === 'active') {
    cd.className = 'countdown'; cd.textContent = (75 - arr.length) + ' left';
  } else {
    cd.className = 'countdown finished'; cd.textContent = 'Finished'; S.gameOver = true;
  }
  // If board view is visible, refresh grids
  if (S.view === 'board') { buildCalledGrid(); renderBoard(); }
}

// ── Called Numbers Grid (1-75) ────────────────────────────────────────────────
// Flat layout: 15 columns × 5 rows = 75 cells, numbered 1-75 left-to-right top-to-bottom
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

function highlightCalledCell(n) {
  const el = $('cn-' + n);
  if (el) el.className = 'num-cell called';
}

// ── Bingo Board ───────────────────────────────────────────────────────────────
// flat[] layout (produced by Python flatten_board):
//   flat[row*5 + col] = the value at display row `row`, display col `col`
//   col 0=B, 1=I, 2=N, 3=G, 4=O
//   FREE cell is at row=2, col=2 → flat[12]

function buildBoards(player) {
  S.boards = [];
  if (player.main_board) {
    S.boards.push({ flat: player.main_board, boardNumber: player.board_numbers?.[0] ?? 0, marked: new Set() });
  }
  (player.extra_boards || []).forEach((eb, i) => {
    S.boards.push({ flat: eb, boardNumber: player.board_numbers?.[i + 1] ?? (100 + i + 1), marked: new Set() });
  });
}

function buildBoardTabs() {
  const sel = $('boardSelector');
  sel.innerHTML = '';
  S.boards.forEach((b, i) => {
    const btn = document.createElement('button');
    btn.className   = 'board-tab' + (i === S.activeBoardIdx ? ' active' : '');
    btn.textContent = i === 0 ? `Board #${b.boardNumber}` : `+Board #${b.boardNumber}`;
    btn.onclick     = () => { S.activeBoardIdx = i; buildBoardTabs(); renderBoard(); };
    sel.appendChild(btn);
  });
}

function renderBoard() {
  const grid  = $('bingoGrid');
  const board = S.boards[S.activeBoardIdx];
  if (!board || !board.flat || board.flat.length < 25) {
    if (grid) grid.innerHTML = '<div style="padding:20px;color:#8b949e;text-align:center;">No board data</div>';
    return;
  }
  grid.innerHTML = '';
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      // ✅ CORRECT INDEX: row-major, matches Python flatten_board output
      const idx      = row * 5 + col;
      const val      = board.flat[idx];
      const isCenter = row === 2 && col === 2;   // idx=12, FREE cell
      const cell     = document.createElement('div');

      if (isCenter || val === null) {
        cell.className   = 'bingo-cell free';
        cell.textContent = '★';
      } else {
        const isCalled = S.calledNumbers.has(val);
        const isMarked = board.marked.has(idx);
        cell.className = 'bingo-cell';
        if (isMarked)       cell.classList.add('marked');
        else if (isCalled)  cell.classList.add('called-available');
        cell.textContent = val;
        cell.onclick = () => tapCell(idx, val);
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
  if (!S.calledNumbers.has(val)) { showToast(`${val} hasn't been called yet!`); return; }
  if (board.marked.has(idx)) board.marked.delete(idx); else board.marked.add(idx);
  renderBoard();
}

// ── BINGO detection ───────────────────────────────────────────────────────────
// All index math uses row*5+col (row-major), FREE cell = idx 12
function checkBingo() {
  if (S.gameOver) return;
  const won = S.boards.some(boardHasBingo);
  S.hasBingo = won;
  const btn = $('bingoBtn');
  if (btn.textContent === '❌ Eliminated') return;
  btn.disabled = !won;
  won ? btn.classList.add('active') : btn.classList.remove('active');
}

function boardHasBingo(board) {
  const FREE_IDX = 12;
  const ok = idx => idx === FREE_IDX || board.flat[idx] === null || board.marked.has(idx);
  // Rows
  for (let r = 0; r < 5; r++)
    if ([0,1,2,3,4].every(c => ok(r * 5 + c))) return true;
  // Columns
  for (let c = 0; c < 5; c++)
    if ([0,1,2,3,4].every(r => ok(r * 5 + c))) return true;
  // Main diagonal (top-left → bottom-right): (0,0)(1,1)(2,2)(3,3)(4,4)
  if ([0,1,2,3,4].every(i => ok(i * 5 + i))) return true;
  // Anti-diagonal (top-right → bottom-left): (0,4)(1,3)(2,2)(3,1)(4,0)
  if ([0,1,2,3,4].every(i => ok(i * 5 + (4 - i)))) return true;
  return false;
}

// ── Game actions ──────────────────────────────────────────────────────────────
async function claimBingo() {
  if (!S.hasBingo || S.gameOver) return;
  const btn = $('bingoBtn');
  btn.disabled = true; btn.textContent = '⏳ Checking…'; btn.classList.remove('active');
  const gameId = S.game?.game_id || GAME_ID;
  const res = await api('/api/game/claim-bingo', {
    method: 'POST',
    body: JSON.stringify({ game_id: gameId, user_id: USER_ID }),
  });
  if (res?.success) {
    showWinModal(res.winner_name, res.board_number, res.amount);
  } else if (res?.eliminated) {
    showToast('❌ False BINGO — you were eliminated!', 4000);
    S.gameOver = true; btn.textContent = '❌ Eliminated';
  } else {
    btn.disabled = false; btn.textContent = '🔴 BINGO!';
    if (S.hasBingo) btn.classList.add('active');
    showToast('🎯 Bingo claim sent!', 2000);
  }
}

function refreshGame() {
  const gameId = S.game?.game_id || GAME_ID;
  if (gameId) pollState(gameId);
  showToast('🔄 Refreshed');
}

function leaveGame() {
  const gameId = S.game?.game_id || GAME_ID;
  if (!gameId) { backToLobby(); return; }
  const doLeave = () => {
    if (S.game?.status === 'waiting') {
      if (tg) tg.sendData(JSON.stringify({ action: 'leave_game', game_id: gameId, user_id: USER_ID }));
      showToast('👋 Left game.', 2000);
      S.gameOver = true;
      setTimeout(backToLobby, 1500);
    } else {
      showToast('⚠️ No refund once game starts.', 3000);
    }
  };
  if (tg?.showConfirm) tg.showConfirm('Leave the game?', ok => { if (ok) doLeave(); });
  else doLeave();
}

function addBoard() {
  const gameId = S.game?.game_id || GAME_ID;
  if (tg) tg.sendData(JSON.stringify({ action: 'add_board', game_id: gameId, user_id: USER_ID }));
  showToast('📋 Open Telegram to confirm extra board (+10 ETB).', 3000);
}

// ── Win Modal ─────────────────────────────────────────────────────────────────
function showWinModal(winner, boardNum, amount) {
  S.gameOver = true; stopPolling();
  $('winnerName').textContent  = winner;
  $('winnerBoard').textContent = boardNum;
  $('winAmount').textContent   = fmt(amount);
  $('winModal').classList.add('show');
}
function playAgain() { $('winModal').classList.remove('show'); S.gameOver = false; backToLobby(); }

// ── Secondary tabs ────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  const data = await api('/api/leaderboard');
  const list = $('leaderboardList');
  if (!data?.players?.length) { list.innerHTML = '<div class="empty-state">No players yet.</div>'; return; }
  const medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  list.innerHTML = data.players.map((p, i) => `
    <div class="list-item">
      <span class="list-rank">${medals[i] || (i+1)}</span>
      <span class="list-name">${esc(p.first_name)}</span>
      <span class="list-value">🏆${p.total_wins} 🪙${p.coin_balance}</span>
    </div>`).join('');
}

async function loadHistory() {
  const data = await api(`/api/history?user_id=${USER_ID}`);
  const list = $('historyList');
  if (!data?.transactions?.length) { list.innerHTML = '<div class="empty-state">No transactions yet.</div>'; return; }
  const icons = {deposit:'💰',withdraw:'💸',win:'🏆',bet:'🎲',refund:'🔄',bonus:'🎁',daily_bonus:'🌟',extra_board:'📋'};
  const sIcons = {approved:'✅',pending:'⏳',rejected:'❌'};
  list.innerHTML = data.transactions.map(tx => `
    <div class="list-item">
      <span>${icons[tx.type]||'💳'}</span>
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
  S.isAdmin = !!u.is_admin;
  $('profileName').textContent      = esc(u.first_name || 'User');
  $('profileUsername').textContent  = u.username ? '@' + u.username : '';
  $('profileWins').textContent      = u.total_wins   || 0;
  $('profileGames').textContent     = u.total_games  || 0;
  $('profileStreak').textContent    = u.win_streak   || 0;
  $('profileDeposits').textContent  = fmt(u.total_deposits  || 0);
  $('profileWithdrawn').textContent = fmt(u.total_withdrawals || 0);
  $('profileCoins').textContent     = u.coin_balance || 0;
  $('profileCode').textContent      = u.referral_code || '-';
  if (S.isAdmin) { $('nav-admin').style.display = ''; $('tab-admin').style.display = ''; }
}

async function refreshWallet() {
  const data = await api(`/api/profile?user_id=${USER_ID}`);
  if (!data?.user) return;
  $('walletBalance').textContent = fmt(data.user.wallet_balance || 0);
  $('walletCoins').textContent   = data.user.coin_balance || 0;
}

function openTelegramLink(action) {
  if (tg) tg.sendData(JSON.stringify({ action, user_id: USER_ID }));
  showToast(`Open Telegram to ${action}.`);
}

// ── Admin ─────────────────────────────────────────────────────────────────────
async function loadAdminDashboard() {
  const stats = await api(`/api/admin/stats?user_id=${USER_ID}`);
  if (stats && !stats.error) {
    $('aStatUsers').textContent    = stats.total_users;
    $('aStatGames').textContent    = stats.active_games;
    $('aStatDeposits').textContent = stats.total_deposits.toFixed(2) + ' ETB';
    $('aStatHouse').textContent    = stats.house_balance.toFixed(2) + ' ETB';
  }
  const data = await api(`/api/admin/pending?user_id=${USER_ID}`);
  const list = $('adminPendingList');
  if (!data?.pending?.length) { list.innerHTML = '<div class="empty-state">✅ No pending approvals.</div>'; return; }
  list.innerHTML = data.pending.map(tx => `
    <div class="admin-tx-card">
      <div class="admin-tx-hdr">
        <span class="admin-tx-name">${esc(tx.first_name)} ${tx.username ? '@'+tx.username : ''}</span>
        <span class="admin-tx-amount">${parseFloat(tx.amount).toFixed(2)} ETB</span>
      </div>
      <div class="admin-tx-meta">
        📌 ${tx.type.toUpperCase()} — ${(tx.created_at||'').slice(0,16)}<br>
        ${tx.transaction_id ? '🔖 ' + esc(tx.transaction_id) : ''}
        ${tx.telebirr_number ? ' 📱 ' + esc(tx.telebirr_number) : ''}
        ${tx.description ? '<br>📝 ' + esc(tx.description) : ''}
      </div>
      <div class="admin-tx-btns">
        <button class="admin-approve-btn" onclick="adminApprove(${tx.id})">✅ Approve</button>
        <button class="admin-reject-btn"  onclick="adminReject(${tx.id})">❌ Reject</button>
      </div>
    </div>`).join('');
}

async function adminApprove(txId) {
  const r = await api('/api/admin/approve', { method:'POST', body:JSON.stringify({ admin_id: USER_ID, tx_id: txId }) });
  if (r?.ok) { showToast('✅ Approved!'); loadAdminDashboard(); }
  else showToast('Error: ' + (r?.error || 'failed'), 3000);
}
async function adminReject(txId) {
  const r = await api('/api/admin/reject', { method:'POST', body:JSON.stringify({ admin_id: USER_ID, tx_id: txId, reason: 'Rejected by admin' }) });
  if (r?.ok) { showToast('❌ Rejected.'); loadAdminDashboard(); }
  else showToast('Error: ' + (r?.error || 'failed'), 3000);
}

// ── Utilities ─────────────────────────────────────────────────────────────────
function fmt(v) { return parseFloat(v || 0).toFixed(2) + ' ETB'; }
function esc(s) { return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;'); }

// ── WS heartbeat ──────────────────────────────────────────────────────────────
setInterval(() => {
  if (S.ws?.readyState === WebSocket.OPEN) S.ws.send(JSON.stringify({ type: 'ping' }));
}, 25000);

// ══════════════════════════════════════════════════════════════════════════════
// INIT
// ══════════════════════════════════════════════════════════════════════════════
async function init() {
  const overlay = $('loadingOverlay');

  if (USER_ID) {
    const profileData = await api(`/api/profile?user_id=${USER_ID}`);
    if (profileData?.user) {
      S.user    = profileData.user;
      S.isAdmin = !!profileData.user.is_admin;
      $('headerBalance').textContent = parseFloat(profileData.user.wallet_balance || 0).toFixed(2);
      $('headerCoins').textContent   = profileData.user.coin_balance || 0;
      if (S.isAdmin) { $('nav-admin').style.display = ''; $('tab-admin').style.display = ''; }
    }
  }

  if (GAME_ID) {
    // Opened with a specific game → board view
    const snap = await api(`/api/game/state?game_id=${GAME_ID}&user_id=${USER_ID}`);
    if (snap?.game)   applyGameSnapshot(snap.game);
    if (snap?.player) buildBoards(snap.player);
    showBoard();
    connectWS(GAME_ID);
  } else {
    showLobby();
  }

  overlay.classList.add('hidden');
  setTimeout(() => overlay.style.display = 'none', 500);
}

document.addEventListener('DOMContentLoaded', init);
