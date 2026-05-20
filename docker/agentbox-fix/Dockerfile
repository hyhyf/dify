FROM langgenius/dify-agentbox:latest

# Fix: conda activation is configured in ~/.bashrc and /etc/bash.bashrc,
# but Dify SSH sandbox executes commands via `sh -lc` (dash login shell),
# which reads ~/.profile and /etc/profile instead of ~/.bashrc.
#
# This ensures conda environment is activated for both login shells (sh/dash)
# and interactive bash shells.

USER root

# Add conda activation to /etc/profile (system-wide, read by sh -l login shell)
RUN echo '' >> /etc/profile && \
    echo '# Activate conda base environment' >> /etc/profile && \
    echo '. /opt/conda/etc/profile.d/conda.sh' >> /etc/profile && \
    echo 'conda activate base 2>/dev/null || true' >> /etc/profile

# Add conda activation to ~/.profile for the agentbox user
# (~/.profile is read by sh -l for user-specific login configuration)
RUN echo '' >> /home/agentbox/.profile && \
    echo '# Activate conda base environment' >> /home/agentbox/.profile && \
    echo '. /opt/conda/etc/profile.d/conda.sh' >> /home/agentbox/.profile && \
    echo 'conda activate base 2>/dev/null || true' >> /home/agentbox/.profile && \
    chown agentbox:agentbox /home/agentbox/.profile

USER agentbox
