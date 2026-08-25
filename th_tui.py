#!/usr/bin/env python3
"""th_tui.py — TokenHarbor Farmer Rich TUI (interactive menu, klik-klik 1/2/3).
Menu visual pakai Rich. Panggil fungsi farm existing (th_tor_farm / th_auto_register).

Run: python th_tui.py
"""
import os, sys, time, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, IntPrompt
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich import box

console = Console()

BANNER = """
[bold cyan]
 ████████╗ ██████╗ ██╗  ██╗███████╗███╗   ██╗██╗  ██╗ █████╗ ██████╗ ██████╗  ██████╗ ██████╗
 ╚══██╔══╝██╔═══██╗██║ ██╔╝██╔════╝████╗  ██║██║ ██╔╝██╔══██╗██╔══██╗██╔══██╗██╔═══██╗██╔══██╗
    ██║   ██║   ██║█████╔╝ █████╗  ██╔██╗ ██║█████╔╝ ███████║██████╔╝██████╔╝██║   ██║██████╔╝
    ██║   ██║   ██║██╔═██╗ ██╔══╝  ██║╚██╗██║██╔═██╗ ██╔══██║██╔══██╗██╔═══╝ ██║   ██║██╔══██╗
    ██║   ╚██████╔╝██║  ██╗███████╗██║ ╚████║██║  ██║██║  ██║██║  ██║██║     ╚██████╔╝██║  ██║
    ╚═╝    ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═══╝╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝╚═╝     ╚═════╝ ╚═╝  ╚═╝
[/bold cyan]
[bold green]TokenHarbor Farmer • Auto Register • Free Models • 9Router Inject[/bold green]
[dim]Tor IP rotation • HTTP Only • Verified 100+ akun[/dim]
"""

def check_tor():
    """Cek Tor hidup (9050)."""
    import socket
    try:
        s = socket.create_connection(("127.0.0.1", 9050), timeout=3)
        s.close()
        return True
    except Exception:
        return False

def main():
    console.print(BANNER, justify="center")
    console.print(Panel("[bold]💡 Status Tools[/bold]", box=box.ROUNDED))
    tor_ok = check_tor()
    console.print(f"  🔥 Tor (9050): {'[green]✅ READY[/green]' if tor_ok else '[red]❌ DOWN — jalankan tor.exe -f torrc[/red]'}")
    # state count
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "th_tor_state.json")
    try:
        st = json.load(open(state_file))
        console.print(f"  📦 Akun tersimpan: [cyan]{len(st.get('accounts', []))}[/cyan]")
    except Exception:
        console.print(f"  📦 Akun tersimpan: [yellow]0 (belum ada state)[/yellow]")
    console.print()

    while True:
        table = Table(title="[bold]📋 MENU[/bold]", box=box.DOUBLE_EDGE, style="cyan")
        table.add_column("No", style="bold yellow", width=4)
        table.add_column("Aksi", style="bold white")
        table.add_column("Keterangan", style="dim")
        rows = [
            ("1", "Register 1 akun", "Signup + verify + consent + key + inject"),
            ("2", "Batch Farm (N)", "Farm massal + auto inject + resume"),
            ("3", "Loop register→logout", "Register, logout, ulang (N kali)"),
            ("4", "Test API key", "Test key terhadap model free"),
            ("5", "Enable Free Models", "Enable free models utk akun existing"),
            ("6", "List Akun", "Lihat akun tersimpan"),
            ("7", "Status 9Router", "Cek conn th + model free"),
            ("0", "Exit", "Keluar"),
        ]
        for r in rows:
            table.add_row(*r)
        console.print(table)
        choice = Prompt.ask("[bold yellow]Pilih menu[/bold yellow]", choices=["0","1","2","3","4","5","6","7"], default="1")

        if choice == "1":
            console.print("[cyan]Register 1 akun...[/cyan]")
            _import_and_run(["single"])
        elif choice == "2":
            n = IntPrompt.ask("[bold]Jumlah akun", default=10)
            inject = Prompt.ask("Inject 9Router?", choices=["y","n"], default="y") == "y"
            args = ["single"] if n == 1 else []
            cmd = ["th_auto_register.py", "loop", str(n)]
            if not inject:
                cmd.append("--no-inject")
            _run_script(cmd)
        elif choice == "3":
            n = IntPrompt.ask("[bold]Jumlah loop", default=5)
            _run_script(["th_auto_register.py", "loop", str(n)])
        elif choice == "4":
            _run_script(["th_auto_register.py", "test"]) if _has_test else console.print("[red]Mode test: gunakan `python th_auto_register.py test`[/red]")
        elif choice == "5":
            console.print("[yellow]Masukkan email & password akun utk enable free models (via script) — atau gunakan farm ulang.[/yellow]")
            _run_script(["th_auto_register.py", "single"])
        elif choice == "6":
            _list_accounts()
        elif choice == "7":
            _status_9router()
        elif choice == "0":
            console.print("[bold green]Bye! 👋[/bold green]")
            break

def _import_and_run(args):
    """Jalankan th_auto_register dgn arg."""
    _run_script(["th_auto_register.py"] + args)

def _run_script(cmd):
    """Jalankan script subprocess + tampilkan output."""
    import subprocess
    base = os.path.dirname(os.path.abspath(__file__))
    console.print(f"[dim]>>> python {' '.join(cmd)}[/dim]")
    try:
        r = subprocess.run([sys.executable] + cmd, cwd=base, timeout=3600)
    except Exception as e:
        console.print(f"[red]Error: {str(e)[:80]}[/red]")
    input("\n[dim]Tekan Enter utk lanjut...[/dim]")

def _list_accounts():
    state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "th_tor_state.json")
    try:
        st = json.load(open(state_file))
        accts = st.get("accounts", [])
        table = Table(title=f"[bold]Akun ({len(accts)})[/bold]", box=box.SIMPLE)
        table.add_column("#", style="dim")
        table.add_column("Email", style="cyan")
        table.add_column("Verified", style="green")
        table.add_column("Consent", style="green")
        table.add_column("Key", style="yellow")
        for i, a in enumerate(accts[-20:], 1):
            table.add_row(str(i), a.get("email","")[:30], "✅" if a.get("verified") else "❌",
                          "✅" if a.get("consent") else "❌", (a.get("api_key","")[:25]+"..."))
        console.print(table)
    except Exception as e:
        console.print(f"[red]State error: {str(e)[:60]}[/red]")
    input("\n[dim]Tekan Enter utk lanjut...[/dim]")

def _status_9router():
    import sqlite3, db_path
    db = db_path.find_9router_db()
    try:
        c = sqlite3.connect(db)
        n = c.execute("SELECT COUNT(*) FROM providerConnections WHERE data LIKE '%tokenharbor.ai%' AND isActive=1").fetchone()[0]
        c.close()
        console.print(f"  🔌 Conn th active: [green]{n}[/green]")
        console.print(f"  🧠 Model free: [cyan]th/mimo-v2.5:free, th/deepseek-v4-flash:free, th/qwen3.8-27b:free[/cyan]")
    except Exception as e:
        console.print(f"[red]DB error: {str(e)[:60]}[/red]")
    input("\n[dim]Tekan Enter utk lanjut...[/dim]")

if __name__ == "__main__":
    main()