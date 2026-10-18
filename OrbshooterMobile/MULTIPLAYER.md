# Orb Tosser multiplayer

The game uses PeerJS Cloud for room signaling and WebRTC data channels for player updates. GitHub Pages can host the HTML and JavaScript; no Node server or WebSocket URL is required. Everyone joins one shared public room named `main`; the room host relays updates to the other players, so the host needs a stable connection.

## Join a game

1. Open the game with internet access.
2. Set your player name in **Settings** if you want to change it.
3. Press **PLAY**. The game automatically joins the shared `main` room and retries if no host is available yet.

Choose **PLAY WITH BOTS** for a solo match that does not connect to multiplayer. The Nuke Launcher is available in solo matches and disabled in multiplayer matches.

The same public room works between a `file://` copy and the GitHub Pages copy. Both need internet access to reach PeerJS Cloud. Human names appear blue on the leaderboard; bot names stay white. Chat masks links, email addresses, common profanity, and the word “discord” with `####` before displaying messages and again before the room host relays them. If a guest loses its connection, it retries the current host. The host keeps the latest 40 messages in memory and sends them to guests when they join or reconnect. That history disappears when the host leaves or reloads; this peer hosted setup has no permanent message store.

PeerJS Cloud is a shared public signaling service, and WebRTC attempts a direct encrypted connection between players. A small number of restrictive networks cannot establish a direct path without a TURN relay; those players may be unable to join. The shared room is capped at eight players. This is a peer hosted room, not a permanent central game server: the first player hosts the room traffic. If that host leaves, guests retry the room and reconnect when a player starts a new host session.
