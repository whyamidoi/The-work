import docker
import uuid
import os
import logging
from flask import Flask, request, redirect, url_for, jsonify, render_template_string

# --- Basic Logging Setup ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# --- Configuration ---
# The base URL where the application is accessible externally.
# CRITICAL: This MUST be set via environment variable in docker-compose.yml
# to the public URL provided by Codespaces for the forwarded port 80.
REVERSE_PROXY_BASE_URL = os.getenv('REVERSE_PROXY_BASE_URL', 'http://localhost') # Default is likely WRONG in Codespace
logging.info(f"Using REVERSE_PROXY_BASE_URL: {REVERSE_PROXY_BASE_URL}")
if REVERSE_PROXY_BASE_URL == 'http://localhost':
    logging.warning("REVERSE_PROXY_BASE_URL is set to default 'http://localhost'. "
                    "This is likely incorrect for Codespaces. Ensure it's set via environment variable.")

FIREFOX_IMAGE = "jlesage/firefox:latest"
# Internal port where noVNC runs inside the jlesage/firefox container (web UI port)
FIREFOX_INTERNAL_PORT = "5800"
# Network shared with the reverse proxy (e.g., Traefik) - must match docker-compose.yml
DOCKER_NETWORK = "proxy_network"
# Example - VNC_PASSWORD could be set via env vars in docker-compose if needed by image
# VNC_PASSWORD = os.getenv("VNC_DEFAULT_PASSWORD", "yoursecurepassword")


# --- Initialize Docker Client ---
try:
    # In Codespaces, docker.from_env() should connect to the Docker daemon available to the Codespace.
    client = docker.from_env()
    client.ping() # Check connection
    logging.info("Successfully connected to Docker daemon.")
except Exception as e:
    logging.error(f"Fatal Error: Could not connect to Docker daemon: {e}")
    # In a real app, you might prevent Flask from starting or show a persistent error page.
    client = None

# Simple in-memory store for active sessions (Replace with DB/Redis in production)
# Format: { session_id: {'container_id': '...', 'container_name': '...'} }
active_sessions = {}

# --- HTML Template for Simple UI ---
HOME_PAGE_HTML = """
<!DOCTYPE html>
<html>
<head>
    <title>Launch Firefox Session (Codespace)</title>
    <style>
        body { font-family: sans-serif; line-height: 1.6; padding: 20px; }
        h1, h2 { border-bottom: 1px solid #ccc; padding-bottom: 5px; }
        ul { list-style: none; padding-left: 0; }
        li { margin-bottom: 10px; padding: 10px; border: 1px solid #eee; border-radius: 4px; }
        button, a { padding: 8px 15px; text-decoration: none; border-radius: 4px; cursor: pointer; }
        button { background-color: #007bff; color: white; border: none; }
        a { display: inline-block; margin-left: 10px; }
        a.open { background-color: #28a745; color: white; }
        a.stop { background-color: #dc3545; color: white; }
        .error { color: red; font-weight: bold; }
        .warning { color: orange; }
    </style>
</head>
<body>
    <h1>Launch a Temporary Firefox Session (in GitHub Codespace)</h1>

    {% if docker_error %}
        <p class="error">FATAL ERROR: Cannot connect to Docker Daemon. The application cannot function.</p>
    {% else %}
        {% if base_url_warning %}
            <p class="warning">Warning: The application's base URL might be misconfigured. Links may not work correctly. Ensure REVERSE_PROXY_BASE_URL is set.</p>
        {% endif %}
        <form action="{{ url_for('launch_firefox') }}" method="post">
            <button type="submit">Launch New Session</button>
        </form>
    {% endif %}

    <h2>Active Sessions:</h2>
    <ul>
    {% for session_id, data in sessions.items() %}
        <li>
            Session <strong>{{ session_id }}</strong>
            (Container: {{ data.container_name }})
            <a class="open" href="{{ proxy_base_url }}session/{{ session_id }}/" target="_blank" title="Opens the Firefox session in a new tab">Open</a>
            <a class="stop" href="{{ url_for('stop_firefox', session_id=session_id) }}" title="Stops and removes the container for this session">Stop</a>
        </li>
    {% else %}
        <li>No active sessions.</li>
    {% endfor %}
    </ul>

    <h2>Logs / Status:</h2>
    <pre style="background-color: #f0f0f0; border: 1px solid #ccc; padding: 10px; max-height: 200px; overflow-y: auto;">{{ status_log | default('No status updates yet.') }}</pre>

</body>
</html>
"""

# Store simple status messages
status_log_messages = []
def add_status(message):
    logging.info(message)
    status_log_messages.insert(0, f"[{os.times().elapsed:.2f}s] {message}")
    # Keep log short for demo purposes
    while len(status_log_messages) > 10:
        status_log_messages.pop()

@app.route('/')
def home():
    base_url_warning = (REVERSE_PROXY_BASE_URL == 'http://localhost')
    return render_template_string(HOME_PAGE_HTML,
                                  sessions=active_sessions,
                                  proxy_base_url=REVERSE_PROXY_BASE_URL,
                                  docker_error=(client is None),
                                  base_url_warning=base_url_warning,
                                  status_log="\n".join(status_log_messages))

@app.route('/launch', methods=['POST'])
def launch_firefox():
    if not client:
        add_status("ERROR: Docker client not available.")
        return redirect(url_for('home'))

    session_id = str(uuid.uuid4())[:8] # Short unique ID
    container_name = f"firefox-session-{session_id}"

    # Define labels for Traefik (adjust rules/service names as needed)
    # Assumes Traefik entrypoint is 'web' and network is DOCKER_NETWORK
    labels = {
        "traefik.enable": "true",
        # Route based on path prefix
        f"traefik.http.routers.{container_name}.rule": f"PathPrefix(`/session/{session_id}`)",
        # Assign to the 'web' entrypoint (defined in docker-compose for Traefik)
        f"traefik.http.routers.{container_name}.entrypoints": "web",
        # Define the service that points to the container
        f"traefik.http.services.{container_name}.loadbalancer.server.port": FIREFOX_INTERNAL_PORT,
        # Optional middleware: Strip the prefix if the app inside doesn't expect it
        # (jlesage/firefox serves from root, so stripping is needed)
        f"traefik.http.routers.{container_name}.middlewares": f"strip-session-{session_id}",
        f"traefik.http.middlewares.strip-session-{session_id}.stripprefix.prefixes": f"/session/{session_id}",
    }

    add_status(f"Attempting to launch container '{container_name}' for session {session_id}")
    try:
        container = client.containers.run(
            image=FIREFOX_IMAGE,
            detach=True,          # Run in background
            auto_remove=True,     # Remove container when stopped (simplifies demo cleanup)
            name=container_name,
            labels=labels,
            network=DOCKER_NETWORK, # Connect to the same network as Traefik
            # Add resource limits in production! e.g.,
            # mem_limit="768m", # Be mindful of Codespace limits!
            # cpu_shares=512,   # Relative CPU weight
            environment={
                 # Example: Set timezone or other vars supported by the image
                 'TZ': 'America/Toronto' # Example timezone from context
            }
        )
        active_sessions[session_id] = {'container_id': container.id, 'container_name': container_name}
        add_status(f"Launched container {container.short_id} ('{container_name}') for session {session_id}")

    except docker.errors.APIError as e:
        add_status(f"ERROR launching container: {e}")
        # Attempt to clean up if container exists but failed mid-start
        try:
            existing_container = client.containers.get(container_name)
            existing_container.remove(force=True)
            add_status(f"Cleaned up potentially failed container '{container_name}'")
        except docker.errors.NotFound:
            pass # Container didn't get created far enough to need removal
        except Exception as cleanup_e:
            add_status(f"ERROR during cleanup of failed container '{container_name}': {cleanup_e}")

    except Exception as e:
        add_status(f"ERROR: Unexpected error launching container: {e}")

    # Redirect back home to see the updated list and status
    return redirect(url_for('home'))

@app.route('/stop/<session_id>')
def stop_firefox(session_id):
    if not client:
        add_status("ERROR: Docker client not available.")
        return redirect(url_for('home'))

    session_info = active_sessions.pop(session_id, None)
    if session_info:
        container_id = session_info['container_id']
        container_name = session_info['container_name']
        add_status(f"Attempting to stop container {container_id} ('{container_name}') for session {session_id}")
        try:
            container = client.containers.get(container_id)
            # Stop with a short timeout. auto_remove=True should handle deletion.
            container.stop(timeout=5)
            add_status(f"Stopped container '{container_name}'")
            # NOTE: If auto_remove=False was used, you would add: container.remove() here.
        except docker.errors.NotFound:
            add_status(f"Warning: Container '{container_name}' already removed or not found.")
        except docker.errors.APIError as e:
            add_status(f"ERROR stopping container '{container_name}': {e}")
            # Decide if you should put session_info back into active_sessions if stop fails
        except Exception as e:
             add_status(f"ERROR: Unexpected error stopping container '{container_name}': {e}")
    else:
        add_status(f"Warning: Session ID {session_id} not found. Cannot stop.")

    return redirect(url_for('home'))

if __name__ == '__main__':
    # Make sure Flask listens on 0.0.0.0 to be accessible within the Docker network
    # Use Waitress or Gunicorn in production instead of Flask's dev server
    # Use debug=False in production
    if client is None:
        print("\n *** Cannot start Flask server: Docker client unavailable. ***\n")
    else:
        print(f"\nStarting Flask controller. Access via reverse proxy URL: {REVERSE_PROXY_BASE_URL}/\n")
        # Use port 5000 internally as configured in docker-compose.yml
        app.run(host='0.0.0.0', port=5000, debug=False)