const { WebSocket } = require('ws');

const projectRooms = new Map();

function getOrCreateRoom(projectId) {
  const key = String(projectId);
  if (!projectRooms.has(key)) {
    projectRooms.set(key, new Map());
  }
  return projectRooms.get(key);
}

function getRoomOnlineMembers(projectId) {
  const room = projectRooms.get(String(projectId));
  if (!room) return [];
  return Array.from(room.values());
}

function buildPresenceMembers(projectId) {
  const members = getRoomOnlineMembers(projectId).filter((m) => (
    m &&
    m.username &&
    m.socket &&
    m.socket.readyState === WebSocket.OPEN
  ));
  const byUsername = new Map();
  members.forEach((m) => {
    const key = String(m.username || '').trim();
    if (!key || byUsername.has(key)) return;
    byUsername.set(key, {
      sessionId: m.sessionId,
      userId: m.userId,
      displayName: m.displayName,
      username: m.username
    });
  });
  return Array.from(byUsername.values());
}

function publishRoomPresence(projectId) {
  const room = projectRooms.get(String(projectId));
  if (!room) return;
  const members = buildPresenceMembers(projectId);
  const payload = JSON.stringify({
    type: 'presence',
    projectId: String(projectId),
    onlineCount: members.length,
    users: members
  });

  room.forEach((member) => {
    if (member.socket && member.socket.readyState === WebSocket.OPEN) {
      member.socket.send(payload);
    }
  });
}

function broadcastToRoom(projectId, senderSessionId, eventBody) {
  const room = projectRooms.get(String(projectId));
  if (!room) return;
  const payload = JSON.stringify(eventBody);
  room.forEach((member) => {
    if (member.sessionId === senderSessionId) return;
    if (member.socket && member.socket.readyState === WebSocket.OPEN) {
      member.socket.send(payload);
    }
  });
}

/**
 * Close all WebSocket connections for a user in a project room (e.g. after member removed).
 */
function disconnectUserSocketsForProject(projectId, userId) {
  const key = String(projectId);
  const room = projectRooms.get(key);
  if (!room) return 0;
  const uid = Number(userId);
  let closed = 0;
  const toDelete = [];
  room.forEach((member, sessionId) => {
    if (Number(member.userId) === uid && member.socket) {
      try {
        member.socket.close(4403, 'forbidden');
      } catch (_) {}
      toDelete.push(sessionId);
      closed += 1;
    }
  });
  toDelete.forEach((sid) => room.delete(sid));
  if (!room.size) {
    projectRooms.delete(key);
  } else {
    publishRoomPresence(projectId);
  }
  return closed;
}

module.exports = {
  projectRooms,
  getOrCreateRoom,
  getRoomOnlineMembers,
  buildPresenceMembers,
  publishRoomPresence,
  broadcastToRoom,
  disconnectUserSocketsForProject,
};
