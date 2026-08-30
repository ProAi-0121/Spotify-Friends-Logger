import whatsappPkg from 'whatsapp-web.js';
const { Client, LocalAuth, MessageMedia } = whatsappPkg;
import express from 'express';
import qrcode from 'qrcode-terminal';
import fs from 'fs';
import path from 'path';
import moment from 'moment';
import axios from 'axios';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// ================= CONFIG =================

const SPOTIFY_GROUP_NAME = process.env.SPOTIFY_GROUP_NAME || "Spotify Friends Logger";
const SPOTIFY_GROUP_ID = process.env.SPOTIFY_GROUP_ID || "";
const API_PORT = process.env.API_PORT || 3939;
const MESSAGE_LOG_FILE = path.join(__dirname, "whatsapp_messages.log");

// Transparent marker for bot messages (zero-width character)
const BOT_MARKER = "\u200b"; // Zero-width space

// ================= WHATSAPP CLIENT =================

const client = new Client({
    authStrategy: new LocalAuth(),
    headless: true,
    puppeteer: {
        args: ['--no-sandbox']
    }
});

let spotifyGroupId = null;
let messageQueue = [];
let isQueueRunning = false;
const QUEUE_DELAY = 5000; // 5 seconds delay between messages

client.on('qr', (qr) => {
    console.log("\n📱 Scan this QR code with WhatsApp:");
    qrcode.generate(qr, { small: true });
});

client.on('ready', () => {
    console.log("✅ WhatsApp client is ready!");
    initializeGroupId();
    startMessageQueueProcessor();
});

client.on('message', async (message) => {
    // Skip bot messages
    if (message.body.includes(BOT_MARKER)) {
        return;
    }
    
    const timestamp = moment().format('YYYY-MM-DD HH:mm:ss');
    const senderName = message.from.includes('@g.us') ? message.author : message.from;
    
    // Get contact info
    const contact = await client.getContactById(message.from);
    const contactName = contact.name || contact.pushname || senderName;
    const contactNumber = senderName.replace('@c.us', '');
    
    // Log message to file
    const logLine = `[${timestamp}] ${contactName}(${contactNumber}): ${message.body}\n`;
    fs.appendFileSync(MESSAGE_LOG_FILE, logLine);
    
    console.log(logLine.trim());
});

client.on('message_create', async (message) => {
    // Log outgoing Spotify messages from bot
    if (message.fromMe && message.to === spotifyGroupId) {
        const timestamp = moment().format('YYYY-MM-DD HH:mm:ss');
        // Remove the invisible marker for logging purposes
        const displayText = message.body.replace(BOT_MARKER, '');
        const logLine = `[${timestamp}] 🤖 BOT: ${displayText}\n`;
        fs.appendFileSync(MESSAGE_LOG_FILE, logLine);
        console.log(logLine.trim());
    }
});

client.on('disconnected', (reason) => {
    console.log('❌ Client disconnected:', reason);
});

// ================= MESSAGE QUEUE SYSTEM =================

async function processMessageQueue() {
    while (true) {
        if (messageQueue.length > 0 && spotifyGroupId) {
            const spotifyData = messageQueue.shift();
            try {
                await sendSpotifyUpdate(spotifyData);
                console.log(`📤 Queue processed: ${messageQueue.length} remaining`);
            } catch (error) {
                console.error("❌ Error processing queue:", error);
            }
            // Wait 5 seconds before sending next message
            await new Promise(resolve => setTimeout(resolve, QUEUE_DELAY));
        } else {
            // Check queue every 500ms if empty
            await new Promise(resolve => setTimeout(resolve, 500));
        }
    }
}

function startMessageQueueProcessor() {
    if (!isQueueRunning) {
        isQueueRunning = true;
        console.log("🔄 Message queue processor started");
        processMessageQueue();
    }
}

// ================= HELPER FUNCTIONS =================

async function initializeGroupId() {
    spotifyGroupId = SPOTIFY_GROUP_ID;
    console.log(`🎧 Using Spotify group ID: ${SPOTIFY_GROUP_ID}`);
    return spotifyGroupId;
}

async function sendSpotifyUpdate(spotifyData) {
    if (!spotifyGroupId) {
        spotifyGroupId = await initializeGroupId();
    }
    
    if (!spotifyGroupId) {
        console.error("❌ Cannot send: Spotify group not found");
        return false;
    }
    
    try {
        const { name, track, artist } = spotifyData;
        const timestamp = moment().format('HH:mm:ss');
        const message = `🎧 *${name}* is listening to:\n\n*${track}*\n-_${artist}_\n\n_\`${timestamp}\`_${BOT_MARKER}`;
        
        await client.sendMessage(spotifyGroupId, message);
        console.log(`✅ Sent to Spotify group: ${name} → ${track}`);
        return true;
    } catch (error) {
        console.error("❌ Error sending message:", error);
        return false;
    }
}

// ================= EXPRESS API SERVER =================

const app = express();
app.use(express.json());

// CORS middleware
app.use((req, res, next) => {
    res.header("Access-Control-Allow-Origin", "*");
    res.header("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
    res.header("Access-Control-Allow-Headers", "Content-Type");
    if (req.method === "OPTIONS") {
        return res.status(200).json({ ok: true });
    }
    next();
});

// Health check
app.get("/health", (req, res) => {
    res.json({
        ok: true,
        whatsapp: client.info ? "connected" : "disconnected",
        spotify_group: spotifyGroupId ? "found" : "not_found"
    });
});

// Receive Spotify updates from Python server
app.post("/spotify", async (req, res) => {
    try {
        const spotifyData = req.body;
        
        if (!spotifyData.name || !spotifyData.track || !spotifyData.artist) {
            return res.status(400).json({
                ok: false,
                error: "Missing required fields: name, track, artist"
            });
        }
        
        // Add to queue instead of sending immediately
        messageQueue.push(spotifyData);
        console.log(`📥 Added to queue: ${spotifyData.name} → ${spotifyData.track} (Queue size: ${messageQueue.length})`);
        
        res.json({ 
            ok: true,
            queued: true,
            queue_size: messageQueue.length
        });
    } catch (error) {
        console.error("❌ Error in /spotify route:", error);
        res.status(500).json({ ok: false, error: error.message });
    }
});

// Get recent logs
app.get("/logs", (req, res) => {
    try {
        const lines = parseInt(req.query.lines) || 50;
        let logs = "";
        
        if (fs.existsSync(MESSAGE_LOG_FILE)) {
            const content = fs.readFileSync(MESSAGE_LOG_FILE, "utf-8");
            const allLines = content.split("\n").filter(l => l.trim());
            logs = allLines.slice(-lines).join("\n");
        }
        
        res.json({ 
            ok: true, 
            logs: logs,
            total_lines: fs.existsSync(MESSAGE_LOG_FILE) ? 
                fs.readFileSync(MESSAGE_LOG_FILE, "utf-8").split("\n").length : 0
        });
    } catch (error) {
        console.error("❌ Error reading logs:", error);
        res.status(500).json({ ok: false, error: error.message });
    }
});

// Get whatsapp status
app.get("/status", (req, res) => {
    res.json({
        ok: true,
        connected: client.info ? true : false,
        spotify_group: spotifyGroupId ? "connected" : "not_found",
        group_name: SPOTIFY_GROUP_NAME,
        queue_size: messageQueue.length,
        queue_running: isQueueRunning
    });
});

// ================= START SERVER =================

client.initialize();

app.listen(API_PORT, () => {
    console.log(`🚀 WhatsApp API Server running on http://localhost:${API_PORT}`);
    console.log(`📝 Message logs saved to: ${MESSAGE_LOG_FILE}`);
});

// Graceful shutdown
process.on("SIGINT", async () => {
    console.log("\n\n👋 Shutting down...");
    await client.destroy();
    process.exit(0);
});

export { client, sendSpotifyUpdate };
