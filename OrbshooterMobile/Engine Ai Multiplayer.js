/* PeerJS/WebRTC room transport. PeerJS Cloud is used only for signaling;
   game traffic travels over peer-to-peer data channels through the room host. */
(function (root) {
    'use strict';

    let peer = null;
    let localId = null;
    let roomId = null;
    let hostConnection = null;
    let localName = 'Player';
    let hostMode = false;
    let statusCallback = null;
    let messageCallback = null;
    const connections = new Map();
    const members = new Map();
    let lastShotSentAt = 0;
    let retryTimer = null;
    let destroyed = false;

    function hashText(value) {
        let hash = 2166136261;
        for (let i = 0; i < value.length; i++) {
            hash ^= value.charCodeAt(i);
            hash = Math.imul(hash, 16777619);
        }
        return (hash >>> 0).toString(36);
    }

    function makeRoomId(room) {
        return `orbroom${hashText(`orb-tosser-2|${String(room).trim().toLowerCase()}`)}`;
    }

    function status(text) { if (statusCallback) statusCallback(text); }
    function receive(message) { if (messageCallback) messageCallback(message); }

    function connect(room, name, onMessage, onStatus) {
        disconnect();
        if (!root.Peer) {
            onStatus('PeerJS did not load. Check your internet connection and reload.');
            return false;
        }
        statusCallback = onStatus;
        messageCallback = onMessage;
        localName = String(name || 'Player').replace(/[<>]/g, '').slice(0, 18) || 'Player';
        roomId = makeRoomId(room);
        destroyed = false;
        hostMode = true;
        status('Looking for the room host…');
        createHostPeer();
        return true;
    }

    function createHostPeer() {
        if (destroyed) return;
        peer = new root.Peer(roomId, { secure: true });
        peer.on('open', id => {
            localId = id;
            hostMode = true;
            members.set(id, { id, name: localName });
            status('Connected as main room host. Waiting for players…');
        });
        peer.on('connection', conn => attachConnection(conn, true));
        peer.on('error', error => {
            if (error.type === 'unavailable-id') {
                peer.destroy();
                peer = null;
                hostMode = false;
                createGuestPeer();
            } else {
                status(error.type === 'peer-unavailable'
                    ? 'Room host not found yet. Retrying…'
                    : `Peer connection error: ${error.type || 'unknown'}`);
            }
        });
        peer.on('disconnected', () => status('Signaling disconnected. Check your internet connection.'));
    }

    function createGuestPeer() {
        if (destroyed) return;
        peer = new root.Peer(undefined, { secure: true });
        peer.on('open', id => {
            localId = id;
            status('Connecting to room host…');
            const conn = peer.connect(roomId, { reliable: true, metadata: { name: localName } });
            attachConnection(conn, false);
        });
        peer.on('connection', conn => attachConnection(conn, false));
        peer.on('error', error => {
            if (error.type === 'peer-unavailable') {
                status('Room host not found yet. Retrying…');
                scheduleHostRetry();
            } else {
                status(`Peer connection error: ${error.type || 'unknown'}`);
            }
        });
        peer.on('disconnected', () => status('Signaling disconnected. Check your internet connection.'));
    }

    function scheduleHostRetry() {
        if (retryTimer || destroyed) return;
        retryTimer = setTimeout(() => {
            retryTimer = null;
            if (destroyed || hostMode || !peer || peer.destroyed) return;
            const conn = peer.connect(roomId, { reliable: true, metadata: { name: localName } });
            attachConnection(conn, false);
        }, 1500);
    }

    function attachConnection(conn, incoming) {
        if (!incoming && !hostMode) hostConnection = conn;
        conn.on('open', () => {
            if (!incoming && !hostMode) conn.send({ type: 'join', name: localName });
        });
        conn.on('data', data => {
            if (!data || typeof data !== 'object') return;
            if (hostMode && incoming && data.type === 'join') {
                const id = conn.peer;
                const name = String(data.name || 'Player').replace(/[<>]/g, '').slice(0, 18) || 'Player';
                if (connections.size > 7) { conn.close(); return; }
                connections.set(id, conn);
                members.set(id, { id, name });
                const players = Array.from(members.values());
                conn.send({ type: 'welcome', id: localId, players });
                receive({ type: 'playerJoined', id, name });
                broadcast({ type: 'playerJoined', id, name }, conn);
                status(`Room open · ${members.size}/8 players`);
                return;
            }

            if (hostMode && incoming) {
                relayFromPeer(conn, data);
            } else {
                if (data.type === 'welcome') {
                    for (const player of data.players || []) members.set(player.id, player);
                    status(`Connected · ${(data.players || []).length}/8 players`);
                }
                receive(data);
                if (data.type === 'playerJoined') members.set(data.id, { id: data.id, name: data.name });
                if (data.type === 'playerLeft') members.delete(data.id);
            }
        });
        conn.on('close', () => {
            const id = conn.peer;
            if (hostMode && incoming && members.has(id)) {
                members.delete(id);
                connections.delete(id);
                receive({ type: 'playerLeft', id });
                broadcast({ type: 'playerLeft', id });
                status(`Room open · ${members.size}/8 players`);
            }
            if (!hostMode && !destroyed) {
                hostConnection = null;
                status('Lost connection to room host. The host must reconnect to create a new room.');
                receive({ type: 'disconnected' });
            }
        });
        conn.on('error', () => {
            if (!hostMode && !destroyed) scheduleHostRetry();
        });
        if (incoming && hostMode) connections.set(conn.peer, conn);
    }

    function relayFromPeer(conn, message) {
        const member = members.get(conn.peer);
        if (!member) return;
        if (message.type === 'rename') {
            member.name = String(message.name || 'Player').replace(/[<>]/g, '').slice(0, 18) || 'Player';
            const renamed = { type: 'playerRenamed', id: member.id, name: member.name };
            receive(renamed);
            broadcast(renamed, conn);
            return;
        }
        if (message.type === 'chat') {
            const text = String(message.text || '').trim().slice(0, 160);
            if (!text) return;
            const chat = { type: 'chat', id: member.id, name: member.name, text };
            receive(chat);
            broadcast(chat, conn);
            return;
        }
        if (message.type === 'hit') {
            const targetId = String(message.targetId || '');
            const hit = { type: 'hit', targetId, damage: Math.max(0, Math.min(200, Number(message.damage) || 0)) };
            if (targetId === localId) receive(hit);
            else {
                const target = connections.get(targetId);
                if (target && target.open) target.send(hit);
            }
            if (conn.open) conn.send({ type: 'hitConfirm', targetId });
            return;
        }
        if (!['state', 'shot', 'score'].includes(message.type)) return;
        const forwarded = Object.assign({}, message, { id: member.id, name: member.name });
        receive(forwarded);
        broadcast(forwarded, conn);
    }

    function broadcast(message, except) {
        connections.forEach(conn => {
            if (conn !== except && conn.open) conn.send(message);
        });
    }

    function send(message) {
        if (!peer || destroyed) return false;
        if (message.type === 'shot') {
            const now = performance.now();
            if (now - lastShotSentAt < 35) return false;
            lastShotSentAt = now;
        }
        if (hostMode) {
            if (message.type === 'hit') {
                const targetId = String(message.targetId || '');
                const hit = { type: 'hit', targetId, damage: Math.max(0, Math.min(200, Number(message.damage) || 0)) };
                if (targetId === localId) receive(hit);
                else {
                    const target = connections.get(targetId);
                    if (target && target.open) target.send(hit);
                }
                receive({ type: 'hitConfirm', targetId });
            } else if (message.type === 'chat') {
                const text = String(message.text || '').trim().slice(0, 160);
                if (!text) return false;
                const chat = { type: 'chat', id: localId, name: localName, text };
                broadcast(chat);
            } else {
                broadcast(Object.assign({}, message, { id: localId, name: localName }));
            }
            return true;
        }
        if (hostConnection && hostConnection.open) {
            hostConnection.send(message);
            return true;
        }
        return false;
    }

    function rename(name) {
        localName = String(name || 'Player').replace(/[<>]/g, '').slice(0, 18) || 'Player';
        if (!peer || destroyed) return false;
        if (hostMode) {
            if (localId && members.has(localId)) members.get(localId).name = localName;
            broadcast({ type: 'playerRenamed', id: localId, name: localName });
            return true;
        }
        if (hostConnection && hostConnection.open) {
            hostConnection.send({ type: 'rename', name: localName });
            return true;
        }
        return false;
    }

    function disconnect() {
        destroyed = true;
        if (retryTimer) clearTimeout(retryTimer);
        retryTimer = null;
        connections.forEach(conn => { try { conn.close(); } catch (_) {} });
        connections.clear();
        members.clear();
        if (peer) { try { peer.destroy(); } catch (_) {} }
        peer = null;
        hostConnection = null;
        localId = null;
        roomId = null;
        hostMode = false;
    }

    root.MultiplayerTransport = { connect, send, rename, disconnect, get playerId() { return localId; } };
})(window);
