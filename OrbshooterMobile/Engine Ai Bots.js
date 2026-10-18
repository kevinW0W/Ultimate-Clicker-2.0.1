/* Lightweight bot navigation and local-neighbor engine for Orb Tosser 2. */
(function (root) {
    'use strict';

    const CELL_SIZE = 24;
    const grid = new Map();
    const offsets = [
        [0, 0], [Math.PI / 4, 0.35], [-Math.PI / 4, 0.35],
        [Math.PI / 2, 0.7], [-Math.PI / 2, 0.7],
        [Math.PI * 3 / 4, 1.05], [-Math.PI * 3 / 4, 1.05], [Math.PI, 1.4]
    ];

    function cellKey(x, z) {
        return `${Math.floor(x / CELL_SIZE)},${Math.floor(z / CELL_SIZE)}`;
    }

    function rebuild(bots) {
        grid.clear();
        for (let i = 0; i < bots.length; i++) {
            const bot = bots[i];
            if (!bot || !bot.userData || bot.userData.hp <= 0) continue;
            const key = cellKey(bot.position.x, bot.position.z);
            let cell = grid.get(key);
            if (!cell) grid.set(key, cell = []);
            cell.push(bot);
        }
    }

    function forEachNearby(x, z, radius, callback) {
        const minX = Math.floor((x - radius) / CELL_SIZE);
        const maxX = Math.floor((x + radius) / CELL_SIZE);
        const minZ = Math.floor((z - radius) / CELL_SIZE);
        const maxZ = Math.floor((z + radius) / CELL_SIZE);
        const radiusSq = radius * radius;
        for (let cx = minX; cx <= maxX; cx++) {
            for (let cz = minZ; cz <= maxZ; cz++) {
                const cell = grid.get(`${cx},${cz}`);
                if (!cell) continue;
                for (let i = 0; i < cell.length; i++) {
                    const other = cell[i];
                    const dx = other.position.x - x;
                    const dz = other.position.z - z;
                    if (dx * dx + dz * dz <= radiusSq) callback(other);
                }
            }
        }
    }

    function findNearestEnemy(bot, radius, initialTarget, initialDistance, isEnemy, canSee) {
        let target = initialTarget;
        let distance = initialDistance;
        forEachNearby(bot.position.x, bot.position.z, radius, other => {
            if (other === bot || !isEnemy(other) || !canSee(bot, other)) return;
            const dx = other.position.x - bot.position.x;
            const dy = other.position.y - bot.position.y;
            const dz = other.position.z - bot.position.z;
            const candidateDistance = Math.sqrt(dx * dx + dy * dy + dz * dz);
            if (candidateDistance < distance) {
                distance = candidateDistance;
                target = other;
            }
        });
        return { target, distance };
    }

    function setYaw(bot, target) {
        const tx = typeof target === 'object' ? target.x : target;
        const tz = typeof target === 'object' ? target.z : arguments[2];
        bot.rotation.y = Math.atan2(tx - bot.position.x, tz - bot.position.z);
    }

    function moveToward(bot, target, delta, checkCollision) {
        const dx = target.x - bot.position.x;
        const dz = target.z - bot.position.z;
        const distanceSq = dx * dx + dz * dz;
        if (distanceSq < 1.44) return true;

        const desiredAngle = Math.atan2(dx, dz);
        const step = bot.userData.speed * delta;
        let bestX = 0, bestZ = 0, bestScore = Infinity, found = false;
        for (let i = 0; i < offsets.length; i++) {
            const angle = desiredAngle + offsets[i][0];
            const x = bot.position.x + Math.sin(angle) * step;
            const z = bot.position.z + Math.cos(angle) * step;
            if (checkCollision(x, z)) continue;
            let occupied = false;
            forEachNearby(x, z, 0.85, other => { if (other !== bot) occupied = true; });
            if (occupied) continue;
            const rx = target.x - x, rz = target.z - z;
            const score = Math.sqrt(rx * rx + rz * rz) + offsets[i][1];
            if (score < bestScore) {
                bestScore = score;
                bestX = x;
                bestZ = z;
                found = true;
            }
        }
        if (!found) return false;
        bot.position.set(bestX, 0.9, bestZ);
        setYaw(bot, target);
        return true;
    }

    root.BotEngine = { rebuild, forEachNearby, findNearestEnemy, setYaw, moveToward };
})(window);
