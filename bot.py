import discord
from discord import app_commands
from discord.ext import commands
import asyncio
import json
import os

TOKEN = ""
OWNER_ID = 931439270454001695   # Only this user can run /create-vps
MACVLAN_NETWORK = "macvlan_pub"
IP_POOL_FILE = "/var/lib/vps-ip-pool/next_ip.txt"
DB_FILE = "/var/lib/vps-db.json"

SYSTEMD_IMAGES = {
    "ubuntu": "darkkop/ubuntu-systemd:22.04",
    "debian": "jrei/systemd-debian:12"
}

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


# ---------------- Utils ---------------- #
async def run_cmd(*args):
    proc = await asyncio.create_subprocess_exec(
        *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await proc.communicate()
    return proc.returncode, out.decode().strip(), err.decode().strip()


def load_db():
    if not os.path.exists(DB_FILE):
        return {}
    with open(DB_FILE, "r") as f:
        return json.load(f)


def save_db(data):
    with open(DB_FILE, "w") as f:
        json.dump(data, f, indent=2)


def allocate_ip():
    os.makedirs(os.path.dirname(IP_POOL_FILE), exist_ok=True)
    with open(IP_POOL_FILE, "r+") as f:
        ip = f.read().strip()
        if not ip:
            raise ValueError("No IP available in pool")
        parts = ip.split(".")
        last = int(parts[-1])
        next_ip = ".".join(parts[:-1] + [str(last + 1)])
        f.seek(0)
        f.write(next_ip)
        f.truncate()
    return ip


async def get_status(vps_name: str):
    code, out, _ = await run_cmd("docker", "inspect", "-f", "{{.State.Status}}", vps_name)
    return out.strip() if code == 0 else "unknown"


# ---------------- Change Password Modal ---------------- #
class ChangePasswordModal(discord.ui.Modal, title="🔑 Change VPS Root Password"):
    def __init__(self, vps_name: str):
        super().__init__()
        self.vps_name = vps_name

    new_password = discord.ui.TextInput(label="New Root Password", style=discord.TextStyle.short)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        code, _, err = await run_cmd(
            "docker", "exec", "-u", "0", self.vps_name,
            "bash", "-lc", f"echo 'root:{self.new_password.value}' | chpasswd"
        )
        if code == 0:
            db = load_db()
            if self.vps_name in db:
                db[self.vps_name]["password"] = self.new_password.value
                save_db(db)
            await interaction.followup.send(f"✅ Password updated for `{self.vps_name}`.", ephemeral=True)
        else:
            await interaction.followup.send(f"❌ Failed: {err}", ephemeral=True)


# ---------------- Manage View ---------------- #
class ManageView(discord.ui.View):
    def __init__(self, vps_name: str, ip: str, owner_id: int):
        super().__init__(timeout=900)
        self.vps_name = vps_name
        self.ip = ip
        self.owner_id = owner_id

    async def update_embed(self, interaction: discord.Interaction, msg: str = None):
        status = await get_status(self.vps_name)
        db = load_db()
        vps = db.get(self.vps_name, {})
        embed = discord.Embed(
            title=f"⚙️ VPS Manager: {self.vps_name}",
            description="Control your VPS with the buttons below:",
            color=discord.Color.blurple()
        )
        embed.add_field(name="📡 Status", value=f"`{status}`", inline=False)
        embed.add_field(name="💻 SSH", value=f"`ssh root@{self.ip}`", inline=False)
        if vps.get("password"):
            embed.add_field(name="🔑 Root Password", value=f"`{vps['password']}`", inline=False)
        embed.set_footer(text="🚀 Powered by AloneHost")

        if msg:
            await interaction.followup.send(msg, ephemeral=True)
        await interaction.message.edit(embed=embed, view=self)

    async def _docker_action(self, interaction: discord.Interaction, action: str):
        await interaction.response.defer(ephemeral=True)
        code, _, err = await run_cmd("docker", action, self.vps_name)
        if code == 0:
            await self.update_embed(interaction, f"✅ VPS `{self.vps_name}` {action}ed.")
        else:
            await interaction.followup.send(f"❌ Failed to {action}: {err}", ephemeral=True)

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success)
    async def start(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger)
    async def stop(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary)
    async def restart(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._docker_action(interaction, "restart")

   # @discord.ui.button(label="Reinstall (Debian 12)", style=discord.ButtonStyle.secondary)
    async def reinstall(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await run_cmd("docker", "rm", "-f", self.vps_name)
        code, _, err = await run_cmd(
            "docker", "run", "-d",
            "--name", self.vps_name,
            "--hostname", self.vps_name,
            "--privileged", "--cgroupns=host",
            "--memory=1g",
            "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
            SYSTEMD_IMAGES["debian"], "/sbin/init"
        )
        if code == 0:
            await run_cmd("docker", "network", "connect", "--ip", self.ip, MACVLAN_NETWORK, self.vps_name)
            await self.update_embed(interaction, f"🔄 VPS `{self.vps_name}` reinstalled with Debian 12.")
        else:
            await interaction.followup.send(f"❌ Reinstall failed: {err}", ephemeral=True)

    @discord.ui.button(label="Change Password", style=discord.ButtonStyle.blurple)
    async def change_password(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(ChangePasswordModal(self.vps_name))

    @discord.ui.button(label="❌ Delete VPS", style=discord.ButtonStyle.red)
    async def delete_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id and interaction.user.id != OWNER_ID:
            await interaction.response.send_message("❌ You are not allowed.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        await run_cmd("docker", "rm", "-f", self.vps_name)
        db = load_db()
        db.pop(self.vps_name, None)
        save_db(db)
        await interaction.followup.send(f"🗑️ VPS `{self.vps_name}` deleted.", ephemeral=True)
        await interaction.message.delete()


# ---------------- Commands ---------------- #
@bot.event
async def on_ready():
    print(f"✅ Logged in as {bot.user}")
    try:
        synced = await bot.tree.sync()
        print(f"✅ Synced {len(synced)} commands")
    except Exception as e:
        print(f"❌ Sync failed: {e}")


@bot.tree.command(name="create-vps", description="Create a new VPS")
async def create_vps(interaction: discord.Interaction, name: str, password: str, owner: discord.Member):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return

    await interaction.response.defer(ephemeral=True)

    try:
        ip = allocate_ip()
    except Exception as e:
        await interaction.followup.send(f"❌ IP allocation failed: {e}", ephemeral=True)
        return

    code, _, err = await run_cmd(
        "docker", "run", "-d",
        "--name", name, "--hostname", name,
        "--privileged", "--cgroupns=host",
        "--memory=1g",
        "-v", "/sys/fs/cgroup:/sys/fs/cgroup:rw",
        SYSTEMD_IMAGES["ubuntu"], "/sbin/init"
    )
    if code != 0:
        await interaction.followup.send(f"❌ VPS create failed: {err}", ephemeral=True)
        return

    await run_cmd("docker", "network", "connect", "--ip", ip, MACVLAN_NETWORK, name)
    await run_cmd("docker", "exec", "-u", "0", name, "bash", "-lc", f"echo 'root:{password}' | chpasswd")

    db = load_db()
    db[name] = {"owner_id": owner.id, "ip": ip, "password": password, "name": name}
    save_db(db)

    try:
        dm = await owner.create_dm()
        embed = discord.Embed(
            title="🌐 Your VPS is Ready!",
            description="Here are your premium server details:",
            color=discord.Color.green()
        )
        embed.add_field(name="🖥️ VPS Name", value=f"`{name}`", inline=False)
        embed.add_field(name="🌍 IP Address", value=f"`{ip}`", inline=False)
        embed.add_field(name="🔑 Root Password", value=f"`{password}`", inline=False)
        embed.add_field(name="💻 SSH Login", value=f"`ssh root@{ip}`", inline=False)
        embed.set_footer(text="🚀 Powered by AloneHost")
        await dm.send(embed=embed)
    except:
        await interaction.followup.send("⚠️ Could not DM owner.", ephemeral=True)

    await interaction.followup.send(f"✅ VPS `{name}` created for {owner.mention} (IP: {ip})", ephemeral=True)


@bot.tree.command(name="manage", description="Manage your VPS")
async def manage(interaction: discord.Interaction, name: str):
    db = load_db()
    vps = db.get(name)
    if not vps:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    if interaction.user.id != vps["owner_id"]:
        await interaction.response.send_message("❌ You are not the owner.", ephemeral=True)
        return

    status = await get_status(name)
    embed = discord.Embed(
        title=f"⚙️ VPS Manager: {name}",
        description="Control your VPS with the buttons below:",
        color=discord.Color.blurple()
    )
    embed.add_field(name="📡 Status", value=f"`{status}`", inline=False)
    embed.add_field(name="💻 SSH", value=f"`ssh root@{vps['ip']}`", inline=False)
    embed.add_field(name="🔑 Root Password", value=f"`{vps['password']}`", inline=False)
    embed.set_footer(text="🚀 Powered by AloneHost")

    await interaction.response.send_message(embed=embed, view=ManageView(name, vps["ip"], vps["owner_id"]), ephemeral=True)


@bot.tree.command(name="delete-vps", description="Admin: Delete a VPS")
async def delete_vps(interaction: discord.Interaction, name: str):
    if interaction.user.id != OWNER_ID:
        await interaction.response.send_message("❌ Not allowed.", ephemeral=True)
        return
    db = load_db()
    if name not in db:
        await interaction.response.send_message("❌ VPS not found.", ephemeral=True)
        return
    await run_cmd("docker", "rm", "-f", name)
    db.pop(name, None)
    save_db(db)
    await interaction.response.send_message(f"🗑️ VPS `{name}` deleted.", ephemeral=True)


@bot.tree.command(name="list", description="List your VPS")
async def list_vps(interaction: discord.Interaction):
    db = load_db()
    user_vps = [n for n, v in db.items() if v["owner_id"] == interaction.user.id]
    if not user_vps:
        await interaction.response.send_message("📭 You have no VPS.", ephemeral=True)
        return
    embed = discord.Embed(title="📋 Your VPS List", color=discord.Color.green())
    embed.add_field(name="Servers", value="\n".join(f"`{n}`" for n in user_vps), inline=False)
    embed.set_footer(text="🚀 Powered by AloneHost")
    await interaction.response.send_message(embed=embed, ephemeral=True)


@bot.tree.command(name="ping", description="Check bot latency")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"🏓 Pong! `{round(bot.latency*1000)}ms`", ephemeral=True)


bot.run(TOKEN)
