from typing import Dict

from lifecycle.monitor.base import LogsStreamer
from racetrack_client.log.logs import get_logger
from racetrack_client.utils.shell import CommandOutputStream, CommandError
from racetrack_commons.deploy.resource import job_resource_name

from plugin_config import InfrastructureConfig

logger = get_logger(__name__)


class DockerDaemonLogsStreamer(LogsStreamer):
    """Source of a Job logs retrieved from a remote Docker container"""

    def __init__(self, infrastructure_target: str, infra_config: InfrastructureConfig) -> None:
        super().__init__()
        self.infra_config = infra_config
        self.infrastructure_target = infrastructure_target
        self.sessions: Dict[str, CommandOutputStream] = {}

    def create_session(self, session_id: str, resource_properties: Dict[str, str]):
        """Start a session transmitting messages to a client."""
        job_name = resource_properties.get('job_name')
        job_version = resource_properties.get('job_version')
        tail = resource_properties.get('tail')
        container_name = job_resource_name(job_name, job_version)

        def on_next_line(line: str):
            self.broadcast(session_id, line)

        def on_error(error: CommandError):
            # Negative return value is the signal number which was used to kill the process. SIGTERM is 15.
            if error.returncode != -15:  # ignore process Terminated on purpose
                logger.error(f'command "{error.cmd}" failed with return code {error.returncode}')

        cmd = f'DOCKER_HOST={self.infra_config.docker_host} docker logs "{container_name}" --follow --tail {tail}'
        output_stream = CommandOutputStream(cmd, on_next_line=on_next_line, on_error=on_error)
        self.sessions[session_id] = output_stream

    def close_session(self, session_id: str):
        output_stream = self.sessions[session_id]
        output_stream.interrupt()
        del self.sessions[session_id]
