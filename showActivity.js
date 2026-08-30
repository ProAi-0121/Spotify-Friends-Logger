(async function () {
    while (!Spicetify?.React || !Spicetify?.Platform) {
        await new Promise(resolve => setTimeout(resolve, 100));
    }

    const USERS_API = "http://192.168.1.67:6767/users";
    const USER_API = "http://192.168.1.67:6767/user/";

    let selectedUserUri = null;
    let selectedUserName = null;

    function timeAgo(dateString) {
        const now = Date.now();

        let past;

        if (typeof dateString === "number") {
            past = dateString;
        } else {
            past = new Date(dateString).getTime();
        }

        const diff = Math.floor((now - past) / 1000);

        if (diff < 60) return `${diff}s ago`;
        if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
        if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;

        return `${Math.floor(diff / 86400)}d ago`;
    }

    function randomImage(seed = "") {
        return `https://picsum.photos/80?random=${encodeURIComponent(seed)}`;
    }

    function createPopup() {
        let overlay = document.getElementById("mongo-activity-popup");

        if (overlay) {
            overlay.remove();
            return;
        }

        overlay = document.createElement("div");

        overlay.id = "mongo-activity-popup";

        overlay.innerHTML = `
            <div class="glass-popup">

                <div class="header">
                    <span id="back-btn">←</span>
                    <h2 id="title">Friends</h2>
                </div>

                <div id="content">
                    Loading...
                </div>

            </div>
        `;

        document.body.appendChild(overlay);

        overlay.onclick = (e) => {
            if (e.target.id === "mongo-activity-popup") {
                overlay.remove();
            }
        };

        document.getElementById("back-btn").onclick = () => {
            if (selectedUserUri) {
                selectedUserUri = null;
                selectedUserName = null;
                loadUsers();
            } else {
                overlay.remove();
            }
        };

        loadUsers();
    }

    let LAST_NOTIFICATION_TIMESTAMP = 0;
    let NOTIFICATION_CACHE = new Set();

    function createRichNotification(item) {
        try {
            const existing = document.getElementById(
                "spotify-friend-toast"
            );

            if (existing) {
                existing.remove();
            }

            const toast = document.createElement("div");

            toast.id = "spotify-friend-toast";

            const trackImage =
                item.track_image ||
                item.image ||
                randomImage(item.name);

            const userPfp =
                item.user_image ||
                randomImage(item.name);

            toast.innerHTML = `
                <div class="friend-toast-inner">

                    <img
                        class="friend-toast-cover"
                        src="${trackImage}"
                    />

                    <div class="friend-toast-content">

                        <div class="friend-toast-top">
                            <img
                                class="friend-toast-pfp"
                                src="${userPfp}"
                            />

                            <span class="friend-toast-user">
                                ${item.name}
                            </span>
                        </div>

                        <div class="friend-toast-track">
                            ${item.track || "Unknown Track"}
                        </div>

                        <div class="friend-toast-artist">
                            ${item.artist || "Unknown Artist"}
                        </div>

                    </div>

                </div>
            `;

            toast.onclick = async () => {
                try {
                    if (item.track_uri) {
                        await Spicetify.Player.playUri(
                            item.track_uri
                        );
                    }

                    toast.remove();

                } catch (e) {
                    console.error(e);
                }
            };

            document.body.appendChild(toast);

            requestAnimationFrame(() => {
                toast.classList.add("show");
            });

            setTimeout(() => {
                toast.classList.remove("show");

                setTimeout(() => {
                    toast.remove();
                }, 300);

            }, 6000);

        } catch (e) {
            console.error(e);
        }
    }

    const USER_TRACK_CACHE = new Map();

    async function checkForNewActivity() {
        try {

            const res = await fetch(USERS_API);

            const users = await res.json();

            if (!Array.isArray(users)) return;

            for (const user of users) {

                const currentTrack =
                    user.last_track || "";

                const currentArtist =
                    user.last_artist || "";

                const cacheKey = `${currentTrack}:::${currentArtist}`;
                const idKey = user.user_uri || user.name;

                const previous = USER_TRACK_CACHE.get(idKey);

                if (!previous) {
                    USER_TRACK_CACHE.set(idKey, cacheKey);
                    continue;
                }

                if (previous !== cacheKey) {
                    USER_TRACK_CACHE.set(idKey, cacheKey);

                    createRichNotification({
                        name: user.name,
                        track: currentTrack,
                        artist: currentArtist,
                        track_image: user.last_track_image,
                        user_image: user.user_image,
                        track_uri: user.last_track_uri,
                        user_uri: idKey
                    });

                    await new Promise(r => setTimeout(r, 2500));
                }
            }

        } catch (e) {
            console.error(
                "[Spotify Friends]",
                e
            );
        }
    }
    setTimeout(() => {
        checkForNewActivity();
    }, 3000);

    setInterval(checkForNewActivity, 8000);

    async function loadUsers() {
        document.getElementById("title").innerText = "Friends";

        const content = document.getElementById("content");

        content.innerHTML = "Loading...";

        try {
            const res = await fetch(USERS_API);

            const users = await res.json();

            content.innerHTML = "";

            users.forEach(user => {
                const div = document.createElement("div");

                div.className = "user-card";

                const fallback = randomImage(user.name || user.user_uri);

                const pfp =
                    user.user_image ||
                    fallback;

                const trackImage =
                    user.last_track_image ||
                    user.track_image ||
                    user.image ||
                    fallback;

                div.innerHTML = `
                    <img class="track-cover" src="${pfp}" />

                    <div class="user-main">

                        <div class="user-header">

                            <div class="user-left">

                                <span class="user-name">
                                    ${user.name}
                                </span>

                            </div>

                            <span class="user-time">
                                ${timeAgo(user.last_seen)}
                            </span>

                        </div>

                        <div class="user-track">
                            ${user.last_track || "Nothing Playing"}
                        </div>

                        <div class="user-artist">
                            ${user.last_artist || ""}
                        </div>

                        <div class="user-sub">
                            ${user.count || 0} plays
                        </div>

                    </div>
                `;

                div.onclick = () => {
                    selectedUserUri = user.user_uri || user.name;
                    selectedUserName = user.name;
                    loadUserHistory();
                };

                content.appendChild(div);
            });

        } catch (err) {
            console.error(err);

            content.innerHTML = "Failed to load users";
        }
    }

    async function setUserHeader() {
        try {
            const res = await fetch(USERS_API);
            const users = await res.json();

            const user = users.find(
                u => (u.user_uri || u.name) === selectedUserUri
            );

            const pfp = user?.user_image || randomImage(selectedUserName || selectedUserUri);

            document.getElementById("title").innerHTML = `
                <div class="title-user">

                    <img
                        class="title-pfp"
                        src="${pfp}"
                    />

                    <span>
                        ${selectedUserName || selectedUserUri}
                    </span>

                </div>
            `;

        } catch {
            document.getElementById("title").innerText =
                (selectedUserName || selectedUserUri);
        }
    }

    function loadUserHistory() {
        setUserHeader();

        const content = document.getElementById("content");

        content.innerHTML = "";

        let offset = 0;
        const limit = 20;

        let loading = false;
        let finished = false;

        async function loadMore() {
            if (loading || finished) return;

            loading = true;

            try {
                const idToFetch = encodeURIComponent(selectedUserUri || selectedUserName || "");
                const res = await fetch(
                    `${USER_API}${idToFetch}?limit=${limit}&offset=${offset}`
                );

                const data = await res.json();

                if (!data.length) {
                    finished = true;
                    return;
                }

                data.forEach(item => {
                    const div = document.createElement("div");

                    div.className = "activity-item";

                    const fallback = randomImage(selectedUserName || selectedUserUri);

                    const trackImage =
                        item.track_image ||
                        item.image ||
                        fallback;

                    const userPfp =
                        item.user_image ||
                        fallback;

                    div.title =
                        item.album ||
                        "Unknown Album";

                    div.innerHTML = `
                        <img class="album" src="${trackImage}" />

                        <div class="activity-main">

                            <div class="activity-header">

                                <div class="activity-left">

                                    <div class="track">
                                        ${item.track || "Unknown Track"}
                                    </div>

                                    <div class="artist">
                                        ${item.artist || "Unknown Artist"}
                                    </div>

                                </div>

                                <span class="time">
                                    ${timeAgo(item.timestamp || item.played_at)}
                                </span>

                            </div>

                            ${
                                item.album
                                    ? `
                                <div class="album-name">
                                    ${item.album}
                                </div>
                            `
                                    : ""
                            }

                        </div>
                    `;

                    div.onclick = async () => {
                        try {
                            if (item.track_uri) {
                                Spicetify.Player.playUri(item.track_uri);
                                return;
                            }

                            const query =
                                `${item.track || ""} ${item.artist || ""}`.trim();

                            const res = await Spicetify.CosmosAsync.post(
                                "sp://search/v2/main",
                                {
                                    searchTerm: query,
                                    offset: 0,
                                    limit: 1,
                                    numberOfTopResults: 1,
                                    includeAudiobooks: false,
                                    includeArtists: false,
                                    includeAlbums: false,
                                    includePlaylists: false,
                                    includePodcasts: false,
                                    includeProfiles: false,
                                    includeGenres: false,
                                }
                            );

                            const uri =
                                res?.tracks?.items?.[0]?.uri ||
                                res?.topResults?.items?.[0]?.uri;

                            if (uri) {
                                Spicetify.Player.playUri(uri);
                            }

                        } catch (err) {
                            console.error(err);
                        }
                    };

                    content.appendChild(div);
                });

                offset += limit;

            } catch (err) {
                console.error(err);
            }

            loading = false;
        }

        loadMore();

        content.onscroll = () => {
            if (
                content.scrollTop + content.clientHeight >=
                content.scrollHeight - 50
            ) {
                loadMore();
            }
        };
    }

    function injectButton() {
        const interval = setInterval(() => {
            const homeBtn = document.querySelector(
                'button[aria-label="Home"]'
            );

            if (!homeBtn) return;

            if (document.getElementById("mongo-activity-btn")) {
                clearInterval(interval);
                return;
            }

            const btn = document.createElement("button");

            btn.id = "mongo-activity-btn";

            btn.className = homeBtn.className;

            btn.setAttribute("aria-label", "Friends");

            btn.innerHTML = `
                <span class="e-10180-button__icon-wrapper">
                    👥
                </span>
            `;

            btn.onclick = createPopup;

            homeBtn.parentNode.insertBefore(btn, homeBtn);

            clearInterval(interval);

        }, 1000);
    }

    setTimeout(() => {

    createRichNotification({
        name: "Spotify Friend Tracker",
        track: "Extension Started Successfully",
        artist: "Listening Activity Enabled",
        track_image:
            "https://i.scdn.co/image/ab67616d0000b2734d5c2e36b2b4f4e6f6f6f6f6",
        user_image:
            "https://i.scdn.co/image/ab6775700000ee85b5d9e4f3f3f3f3f3f3f3f3f",
        track_uri:
            "spotify:track:4uLU6hMCjMI75M1A2tKUQC"
    });

}, 1800);

    injectButton();

    const style = document.createElement("style");

    style.textContent = `
        #mongo-activity-popup {
            position: fixed;
            inset: 0;
            background: rgba(0,0,0,0.6);
            backdrop-filter: blur(24px);
            display: flex;
            justify-content: center;
            align-items: center;
            z-index: 999999;
        }

        .glass-popup {
            width: 760px;
            max-height: 88%;
            overflow: hidden;
            border-radius: 24px;
            padding: 24px;
            background: rgba(18,18,18,0.72);
            backdrop-filter: blur(30px);
            border: 1px solid rgba(255,255,255,0.06);
            box-shadow: 0 0 80px rgba(0,0,0,0.7);
        }

        .header {
            display: flex;
            align-items: center;
            gap: 14px;
            margin-bottom: 18px;
        }

        #back-btn {
            cursor: pointer;
            font-size: 22px;
            opacity: 0.7;
            transition: 0.2s ease;
        }

        #back-btn:hover {
            opacity: 1;
            transform: translateX(-2px);
        }

        #content {
            max-height: 720px;
            overflow-y: auto;
            padding-right: 6px;
        }

        #content::-webkit-scrollbar {
            width: 8px;
        }

        #content::-webkit-scrollbar-thumb {
            background: rgba(255,255,255,0.12);
            border-radius: 999px;
        }

        .user-card,
        .activity-item {
            display: flex;
            gap: 16px;
            align-items: center;
            padding: 14px;
            margin-bottom: 14px;
            border-radius: 18px;
            background: rgba(255,255,255,0.045);
            transition: all 0.2s ease;
            cursor: pointer;
        }

        .user-card:hover,
        .activity-item:hover {
            background: rgba(255,255,255,0.08);
            transform: scale(1.01);
        }

        .track-cover,
        .album {
            width: 76px;
            height: 76px;
            border-radius: 16px;
            object-fit: cover;
            box-shadow: 0 0 20px rgba(0,0,0,0.45);
            flex-shrink: 0;
        }

        .user-main,
        .activity-main {
            flex: 1;
            min-width: 0;
        }

        .user-header,
        .activity-header {
            display: flex;
            align-items: center;
            justify-content: space-between;
            gap: 12px;
        }

        .user-time,
        .time {
            font-size: 12px;
            opacity: 0.6;
            white-space: nowrap;
            flex-shrink: 0;
        }

        .user-track,
        .track {
            margin-top: 6px;
            font-size: 17px;
            font-weight: 700;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .user-artist,
        .artist {
            margin-top: 2px;
            font-size: 13px;
            opacity: 0.78;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .user-sub,
        .album-name {
            margin-top: 5px;
            font-size: 12px;
            opacity: 0.5;
        }

        .title-user {
            display: flex;
            align-items: center;
            gap: 12px;
        }

        .title-pfp {
            width: 34px;
            height: 34px;
            border-radius: 50%;
            object-fit: cover;
        }
        .user-name,
        .activity-user-name {
            font-weight: 900;
            font-size: 17px;
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        .activity-left {
            display: flex;
            flex-direction: column;
            min-width: 0;
        }

        .activity-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-start;
            gap: 12px;
        }

        #spotify-friend-toast {
    position: fixed;
    top: 24px;
    right: 24px;
    width: 340px;
    z-index: 999999999;

    opacity: 0;
    transform: translateY(-20px) scale(0.96);

    transition:
        opacity 0.28s ease,
        transform 0.28s ease;

    pointer-events: auto;
    }

    #spotify-friend-toast.show {
        opacity: 1;
        transform: translateY(0) scale(1);
    }

    .friend-toast-inner {
        display: flex;
        gap: 14px;

        padding: 14px;

        border-radius: 20px;

        background:
            rgba(20,20,20,0.82);

        backdrop-filter: blur(28px);

        border:
            1px solid rgba(255,255,255,0.06);

        box-shadow:
            0 0 40px rgba(0,0,0,0.45);

        cursor: pointer;

        overflow: hidden;
    }

    .friend-toast-cover {
        width: 74px;
        height: 74px;

        border-radius: 16px;

        object-fit: cover;

        flex-shrink: 0;

        box-shadow:
            0 0 18px rgba(0,0,0,0.45);
    }

    .friend-toast-content {
        flex: 1;
        min-width: 0;

        display: flex;
        flex-direction: column;
        justify-content: center;
    }

    .friend-toast-top {
        display: flex;
        align-items: center;
        gap: 8px;

        margin-bottom: 6px;
    }

    .friend-toast-pfp {
        width: 22px;
        height: 22px;

        border-radius: 50%;

        object-fit: cover;
    }

    .friend-toast-user {
        font-size: 13px;
        font-weight: 700;

        opacity: 0.88;
    }

    .friend-toast-track {
        font-size: 15px;
        font-weight: 800;

        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .friend-toast-artist {
        margin-top: 2px;

        font-size: 13px;

        opacity: 0.7;

        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }
    `;

    document.head.appendChild(style);

    Spicetify.showNotification("Friends UI Loaded 🚀");

})();