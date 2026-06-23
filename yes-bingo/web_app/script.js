'use strict';

// ─── Telegram WebApp Init ────────────────────────────────────────────────────
const tg = window.Telegram?.WebApp;
if (tg) {
  tg.ready();
  tg.expand();
  tg.enableClosingConfirmation();
}

// ─── State ───────────────────────────────────────────────────────────────────
const state = {
  user: null,
  game: null,
  player: null,
  boards: [],          // [{flat, boardNumber, marked: Set}]
  activeBoardIdx: 0,
  calledNumbers: new Set(),
  currentCall: null,
  countdown: null,
  pollInterval: null,
  hasBingo: false,
  gameOver: false,
};

// ─── URL Params ───────────────────────────────────────────────────────────────
const params = new URLSearchParams(window.location.search);
const GAME_ID  = params.get('game_id');
const USER_ID  = params.get('user_id');
const API_BASE = params.get('api') || '';   // optional backend base

// ─── DOM Refs ─────────────────────────────────────────────────────────────────
const $ = id => document.getElementById(id);

// ─── Toast ───────────────────────────────────────────────────────────────────
let toastTimer = null;
function showToast(msg, duration = 2500) {
  const el = $('toast');
  el.textContent = msg;
  el.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove('show'), duration);
}

// ─── Tab Navigation ───────────────────────────────────────────────────────────
function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  $('tab-' + name).classList.add('active');
  $('nav-' + name).classList.add('active');
  if (name === 'scores') loadLeaderboard();
  if (name === 'history') loadHistory();
  if (name === 'profile') loadProfile();
  if (name === 'wallet') updateWalletUI();
}

// ─── API helpers ─────────────────────────────────────────────────────────────
async function apiFetch(path, opts = {}) {
  try {
    const res = await fetch(API_BASE + path, {
      headers: { 'Content-Type': 'application/json', 'X-User-Id': USER_ID || '' },
      ...opts,
    });
    if (!res.ok) return null;
    return await res.json();
  } catch { return null; }
}

// ─── Called Numbers Grid ──────────────────────────────────────────────────────
function buildCalledGrid() {
  const grid = $('calledGrid');
  grid.innerHTML = '';
  for (let i = 1; i <= 75; i++) {
    const cell = document.createElement('div');
    cell.className = 'num-cell' + (state.calledNumbers.has(i) ? ' called' : '');
    cell.textContent = i;
    cell.id = 'cn-' + i;
    grid.appendChild(cell);
  }
}

function updateCalledGrid(newNumbers) {
  for (const n of newNumbers) {
    const el = $('cn-' + n);
    if (el && !el.classList.contains('called')) {
      el.classList.add('called');
    }
  }
}

// ─── Bingo Board ──────────────────────────────────────────────────────────────
function buildBoardTabs() {
  const sel = $('boardSelector');
  sel.innerHTML = '';
  state.boards.forEach((b, i) => {
    const tab = document.createElement('button');
    tab.className = 'board-tab' + (i === state.activeBoardIdx ? ' active' : '');
    tab.textContent = i === 0 ? `Board #${b.boardNumber}` : `Extra #${b.boardNumber}`;
    tab.onclick = () => { state.activeBoardIdx = i; renderBoard(); buildBoardTabs(); };
    sel.appendChild(tab);
  });
}

function renderBoard() {
  const grid = $('bingoGrid');
  grid.innerHTML = '';
  const board = state.boards[state.activeBoardIdx];
  if (!board) return;

  // flat is column-major: flat[col*5+row]
  for (let row = 0; row < 5; row++) {
    for (let col = 0; col < 5; col++) {
      const idx = col * 5 + row;
      const val = board.flat[idx];
      const cell = document.createElement('div');
      const isCenter = row === 2 && col === 2;

      if (isCenter || val === null) {
        cell.className = 'bingo-cell free';
        cell.textContent = '★';
      } else {
        const isCalled = state.calledNumbers.has(val);
        const isMarked = board.marked.has(idx);
        cell.className = 'bingo-cell';
        if (isMarked) cell.classList.add('marked');
        else if (isCalled) cell.classList.add('called-available');
        cell.textContent = val;
        cell.onclick = () => handleCellTap(idx, val);
      }
      grid.appendChild(cell);
    }
  }

  $('statBoard').textContent = board.boardNumber;
  checkBingoStatus();
}

function handleCellTap(idx, val) {
  if (state.gameOver) return;
  const board = state.boards[state.activeBoardIdx];
  if (!board) return;
  if (!state.calledNumbers.has(val)) {
    showToast(`${val} hasn't been called yet!`);
    return;
  }
  if (board.marked.has(idx)) {
    board.marked.delete(idx);
  } else {
    board.marked.add(idx);
  }
  renderBoard();
}

// ─── BINGO Check ──────────────────────────────────────────────────────────────
function checkBingoStatus() {
  if (state.gameOver) return;
  let hasBingo = false;
  for (const board of state.boards) {
    if (boardHasBingo(board)) { hasBingo = true; break; }
  }
  state.hasBingo = hasBingo;
  const btn = $('bingoBtn');
  if (hasBingo) {
    btn.disabled = false;
    btn.classList.add('active');
    btn.textContent = '🔴 BINGO!';
  } else {
    btn.disabled = true;
    btn.classList.remove('active');
    btn.textContent = '🔴 BINGO!';
  }
}

function boardHasBingo(board) {
  const isMarkedOrFree = idx => {
    const val = board.flat[idx];
    const isCenter = idx === 12; // row2 col2 = 2*5+2
    return isCenter || val === null || board.marked.has(idx);
  };

  // Rows
  for (let row = 0; row < 5; row++) {
    if ([0,1,2,3,4].every(col => isMarkedOrFree(col * 5 + row))) return true;
  }
  // Cols
  for (let col = 0; col < 5; col++) {
    if ([0,1,2,3,4].every(row => isMarkedOrFree(col * 5 + row))) return true;
  }
  // Diagonals
  if ([0,1,2,3,4].every(i => isMarkedOrFree(i * 5 + i))) return true;
  if ([0,1,2,3,4].every(i => isMarkedOrFree((4 - i) * 5 + i))) return true;
  return false;
}

// ─── Game Actions ─────────────────────────────────────────────────────────────
async function claimBingo() {
  if (!state.hasBingo || state.gameOver) return;
  const btn = $('bingoBtn');
  btn.disabled = true;
  btn.textContent = '⏳ Checking...';

  const result = await apiFetch(`/api/game/claim-bingo`, {
    method: 'POST',
    body: JSON.stringify({ game_id: GAME_ID, user_id: USER_ID }),
  });

  if (result) {
    if (result.success) {
      showWinModal(result.winner_name || 'You', result.board_number, result.amount);
    } else if (result.eliminated) {
      showToast('❌ False BINGO! You have been eliminated.', 4000);
      state.gameOver = true;
      btn.textContent = '❌ Eliminated';
    } else {
      showToast(result.message || 'Could not claim BINGO.', 3000);
      btn.disabled = false;
      btn.textContent = '🔴 BINGO!';
      if (state.hasBingo) btn.classList.add('active');
    }
  } else {
    // No API endpoint yet — show via Telegram sendData
    if (tg) {
      tg.sendData(JSON.stringify({ action: 'claim_bingo', game_id: GAME_ID, user_id: USER_ID }));
    }
    showToast('🎯 BINGO claim sent!', 2000);
  }
}

async function leaveGame() {
  if (tg) {
    tg.showConfirm('Leave the game? Refund only available before the game starts.', (ok) => {
      if (ok) {
        if (state.game?.status === 'waiting') {
          tg.sendData(JSON.stringify({ action: 'leave_game', game_id: GAME_ID, user_id: USER_ID }));
          showToast('Left game. Refund issued.', 2000);
          setTimeout(() => tg.close(), 2000);
        } else {
          showToast('No refund after game starts.', 2500);
        }
      }
    });
  }
}

async function addBoard() {
  if (tg) {
    tg.sendData(JSON.stringify({ action: 'add_board', game_id: GAME_ID, user_id: USER_ID }));
    showToast('Extra board request sent. Check Telegram for confirmation.', 3000);
  }
}

function refreshGame() {
  loadGameState();
  showToast('🔄 Refreshed');
}

// ─── Win Modal ────────────────────────────────────────────────────────────────
function showWinModal(winner, boardNum, amount) {
  state.gameOver = true;
  stopPolling();
  $('winnerName').textContent = winner;
  $('winnerBoard').textContent = boardNum;
  $('winAmount').textContent = parseFloat(amount || 0).toFixed(2) + ' ETB';
  $('winModal').classList.add('show');
}

function closeWinModal() {
  $('winModal').classList.remove('show');
  if (tg) tg.close();
}

// ─── Game State Load ──────────────────────────────────────────────────────────
async function loadGameState() {
  if (!GAME_ID || !USER_ID) {
    renderDemoGame();
    return;
  }

  const data = await apiFetch(`/api/game/state?game_id=${GAME_ID}&user_id=${USER_ID}`);
  if (!data) {
    renderDemoGame();
    return;
  }

  state.game = data.game;
  state.player = data.player;

  const newCalled = data.game.called_numbers || [];
  const prevSize = state.calledNumbers.size;
  newCalled.forEach(n => state.calledNumbers.add(n));

  if (state.calledNumbers.size > prevSize) {
    updateCalledGrid(newCalled);
    const last = newCalled[newCalled.length - 1];
    if (last) {
      state.currentCall = last;
      $('currentCall').textContent = last;
    }
  }

  if (state.boards.length === 0 && data.player) {
    buildBoards(data.player);
  } else {
    renderBoard();
  }

  updateGameInfoUI(data.game, data.player);

  if (data.game.status === 'finished' && data.game.winner_id) {
    const wname = data.winner_name || 'Someone';
    showWinModal(wname, data.winner_board || '-', data.winner_amount || 0);
  }
}

function buildBoards(player) {
  state.boards = [];
  if (player.main_board) {
    state.boards.push({ flat: player.main_board, boardNumber: player.board_numbers?.[0] || 0, marked: new Set() });
  }
  if (player.extra_boards) {
    player.extra_boards.forEach((eb, i) => {
      state.boards.push({ flat: eb, boardNumber: player.board_numbers?.[i + 1] || (100 + i + 1), marked: new Set() });
    });
  }
  buildBoardTabs();
  renderBoard();
}

function updateGameInfoUI(game, player) {
  $('gameId').textContent = game.game_id || GAME_ID;
  $('statPlayers').textContent = `${game.player_count}/${100}`;
  $('statPot').textContent = parseFloat(game.total_pot || 0).toFixed(2) + ' ETB';
  const prize = (parseFloat(game.total_pot || 0) * 0.8);
  $('statPrize').textContent = prize.toFixed(2) + ' ETB';

  const status = game.status;
  const cdEl = $('countdown');
  if (status === 'waiting') {
    cdEl.className = 'countdown waiting';
    cdEl.textContent = 'Waiting';
  } else if (status === 'active') {
    cdEl.className = 'countdown';
    cdEl.textContent = `${75 - (game.called_numbers?.length || 0)} left`;
  } else {
    cdEl.className = 'countdown finished';
    cdEl.textContent = 'Finished';
  }
}

// ─── Demo Game (no backend) ───────────────────────────────────────────────────
function renderDemoGame() {
  $('gameId').textContent = 'G' + (3700 + Math.floor(Math.random() * 300));
  $('countdown').textContent = '25s';
  $('countdown').className = 'countdown waiting';
  $('statPlayers').textContent = '2/100';
  $('statPot').textContent = '20.00 ETB';
  $('statPrize').textContent = '16.00 ETB';

  const demoBoard = generateDemoBoard();
  state.boards = [{ flat: demoBoard, boardNumber: 37, marked: new Set() }];
  buildBoardTabs();
  buildCalledGrid();
  renderBoard();
}

function generateDemoBoard() {
  const ranges = [[1,15],[16,30],[31,45],[46,60],[61,75]];
  const flat = [];
  for (let col = 0; col < 5; col++) {
    const [lo, hi] = ranges[col];
    const nums = shuffled(Array.from({length: hi-lo+1}, (_,i)=>i+lo)).slice(0,5);
    for (let row = 0; row < 5; row++) flat.push(nums[row]);
  }
  flat[12] = null; // FREE center
  return flat;
}

function shuffled(arr) {
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr;
}

// ─── Leaderboard ─────────────────────────────────────────────────────────────
async function loadLeaderboard() {
  const data = await apiFetch(`/api/leaderboard`);
  const list = $('leaderboardList');
  if (!data || !data.players?.length) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#8b949e;">No data yet.</div>';
    return;
  }
  const medals = ['🥇','🥈','🥉','4️⃣','5️⃣','6️⃣','7️⃣','8️⃣','9️⃣','🔟'];
  list.innerHTML = data.players.map((p, i) => `
    <div class="list-item">
      <span class="list-rank">${medals[i] || (i+1)}</span>
      <span class="list-name">${escHtml(p.first_name)}</span>
      <span class="list-value">🪙 ${p.coin_balance} | 🏆 ${p.total_wins}</span>
    </div>
  `).join('');
}

// ─── History ──────────────────────────────────────────────────────────────────
async function loadHistory() {
  const data = await apiFetch(`/api/history?user_id=${USER_ID}`);
  const list = $('historyList');
  if (!data || !data.transactions?.length) {
    list.innerHTML = '<div style="padding:20px;text-align:center;color:#8b949e;">No transactions yet.</div>';
    return;
  }
  const icons = {deposit:'💰',withdraw:'💸',win:'🏆',bet:'🎲',refund:'🔄',bonus:'🎁',credit_conversion:'🪙',extra_board:'📋',daily_bonus:'🌟'};
  const statusIcons = {approved:'✅',pending:'⏳',rejected:'❌'};
  list.innerHTML = data.transactions.map(tx => `
    <div class="list-item">
      <span>${icons[tx.type] || '💳'}</span>
      <span class="list-name">
        <span class="tx-type tx-${tx.type}">${tx.type.toUpperCase()}</span>
        <div style="font-size:11px;color:#8b949e;">${tx.created_at?.slice(0,16) || ''}</div>
      </span>
      <span class="list-value">${parseFloat(tx.amount).toFixed(2)} ETB ${statusIcons[tx.status] || ''}</span>
    </div>
  `).join('');
}

// ─── Profile ──────────────────────────────────────────────────────────────────
async function loadProfile() {
  const data = await apiFetch(`/api/profile?user_id=${USER_ID}`);
  if (!data) return;
  const u = data.user;
  $('profileName').textContent = u.first_name || 'User';
  $('profileUsername').textContent = u.username ? '@' + u.username : '';
  $('profileWins').textContent = u.total_wins || 0;
  $('profileGames').textContent = u.total_games || 0;
  $('profileStreak').textContent = u.win_streak || 0;
  $('profileDeposits').textContent = parseFloat(u.total_deposits || 0).toFixed(2) + ' ETB';
  $('profileWithdrawn').textContent = parseFloat(u.total_withdrawals || 0).toFixed(2) + ' ETB';
  $('profileCoins').textContent = u.coin_balance || 0;
  $('profileCode').textContent = u.referral_code || '-';
}

// ─── Wallet UI ────────────────────────────────────────────────────────────────
function updateWalletUI() {
  if (state.user) {
    $('walletBalance').textContent = parseFloat(state.user.wallet_balance || 0).toFixed(2) + ' ETB';
    $('walletCoins').textContent = state.user.coin_balance || 0;
  }
}

function openTelegramLink(action) {
  if (tg) tg.sendData(JSON.stringify({ action, user_id: USER_ID }));
  showToast(`Opening ${action} in Telegram...`);
}

// ─── Header Balance ───────────────────────────────────────────────────────────
async function loadUserBalance() {
  const data = await apiFetch(`/api/profile?user_id=${USER_ID}`);
  if (data?.user) {
    state.user = data.user;
    $('headerBalance').textContent = parseFloat(data.user.wallet_balance || 0).toFixed(2);
    $('headerCoins').textContent = data.user.coin_balance || 0;
  }
}

// ─── Countdown Timer ─────────────────────────────────────────────────────────
function startCountdown(seconds) {
  clearInterval(state.countdown);
  let remaining = seconds;
  const cdEl = $('countdown');
  cdEl.className = 'countdown waiting';
  cdEl.textContent = remaining + 's';
  state.countdown = setInterval(() => {
    remaining--;
    if (remaining <= 0) {
      clearInterval(state.countdown);
      cdEl.textContent = '0s';
    } else {
      cdEl.textContent = remaining + 's';
    }
  }, 1000);
}

// ─── Polling ──────────────────────────────────────────────────────────────────
function startPolling() {
  stopPolling();
  state.pollInterval = setInterval(() => {
    if (!state.gameOver) loadGameState();
  }, 3000);
}

function stopPolling() {
  clearInterval(state.pollInterval);
}

// ─── Utility ─────────────────────────────────────────────────────────────────
function escHtml(s) {
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

// ─── Init ─────────────────────────────────────────────────────────────────────
async function init() {
  const overlay = $('loadingOverlay');

  // Apply Telegram theme
  if (tg?.colorScheme === 'light') {
    document.documentElement.style.setProperty('--bg-primary', '#f5f5f5');
    document.documentElement.style.setProperty('--bg-secondary', '#ffffff');
    document.documentElement.style.setProperty('--bg-card', '#f0f0f0');
    document.documentElement.style.setProperty('--text-primary', '#111');
    document.documentElement.style.setProperty('--text-secondary', '#555');
    document.documentElement.style.setProperty('--border', '#ddd');
  }

  buildCalledGrid();
  await loadGameState();
  await loadUserBalance();

  if (GAME_ID && !state.gameOver) {
    startPolling();
  }

  overlay.classList.add('hidden');
  setTimeout(() => overlay.style.display = 'none', 500);
}

document.addEventListener('DOMContentLoaded', init);
