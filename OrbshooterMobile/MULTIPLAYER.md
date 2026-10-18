# Orb Tosser multiplayer

The game uses PeerJS Cloud for room signaling and WebRTC data channels for player updates. GitHub Pages can host the HTML and JavaScript; no Node server or WebSocket URL is required. Everyone joins one shared public room named `main`; the room host relays updates to the other players, so the host needs a stable connection.

## Join a game

1. Open the game with internet access.
2. Set your player name in **Settings** if you want to change it.
3. Press **PLAY**. The game automatically joins the shared `main` room and retries if no host is available yet.

Choose **PLAY WITH BOTS** for a solo match that does not connect to multiplayer. The Nuke Launcher is available in solo matches and disabled in multiplayer matches.

The same public room works between a `file://` copy and the GitHub Pages copy. Both need internet access to reach PeerJS Cloud. Human names appear blue on the leaderboard; bot names stay white. Chat masks links, email addresses, common profanity, and the word “discord” with `####` before displaying messages and again before the room host relays them.

PeerJS Cloud is a shared public signaling service, and WebRTC attempts a direct encrypted connection between players. A small number of restrictive networks cannot establish a direct path without a TURN relay; those players may be unable to join. The shared room is capped at eight players. This is a peer hosted room, not a permanent central game server: the first player hosts the room traffic, so leaving that session disconnects its guests and another player must start a new session.
