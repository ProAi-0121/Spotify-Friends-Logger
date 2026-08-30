// NAME: FriendActivitySniffer
// DESCRIPTION: Sends full page text to backend for parsing

(function FriendActivitySniffer() {
    const ENDPOINT = "http://127.0.0.1:5000";

    // 🔥 Startup ping
    fetch(ENDPOINT + "/ping", { method: "POST" }).catch(() => {});

    function wait() {
        if (!document.body) {
            setTimeout(wait, 500);
            return;
        }

        console.log("FriendActivitySniffer running (FULL PAGE MODE)");

        function sendPage() {
            const payload = {
                time: new Date().toISOString(),
                text: document.body.innerText.slice(0, 500_000) // safety limit
                // html: document.body.innerHTML.slice(0, 500_000) // optional
            };

            fetch(ENDPOINT + "/page", {
                method: "POST",
                mode: "cors",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload)
            }).catch(() => {});

        }

        // 🔁 send every 20 seconds
        setInterval(sendPage, 20000);
    }

    wait();
})();
