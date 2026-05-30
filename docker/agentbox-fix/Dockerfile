FROM langgenius/dify-agentbox:latest

# The base image may have conda activation in profile files from prior
# agentbox-fix builds. We clean those and replace with fast static exports.
#
# Problem: `sh -lc` (SSH login shell used by Dify sandbox) reads
# /etc/profile and ~/.profile. These previously contained:
#   . /opt/conda/etc/profile.d/conda.sh
#   conda activate base
# which takes ~1.5s each (sourcing hundreds of shell functions).
#
# Fix: remove all conda.sh sourcing, replace with static env var exports.
#       add /opt/conda/bin to PATH in /etc/environment.

USER root

# 1. Remove ALL conda activation from /etc/profile (clean slate, then add fast version)
RUN sed -i '/conda\.sh\|conda activate\|__conda_setup\|conda initialize/d' /etc/profile 2>/dev/null || true

# 2. Remove conda activation from user .profile
RUN sed -i '/conda\.sh\|conda activate\|__conda_setup\|conda initialize/d' /home/agentbox/.profile 2>/dev/null || true

# 3. Remove conda init block from .bashrc (conda init bash injected code)
RUN sed -i '/>>> conda initialize >>>/,/<<< conda initialize <<</d' /home/agentbox/.bashrc 2>/dev/null || true

# 4. Also remove explicit conda.sh lines from .bashrc (from 07-configure-user.sh)
RUN sed -i '/conda\.sh\|conda activate/d' /home/agentbox/.bashrc 2>/dev/null || true

# 5. Ensure /etc/bash.bashrc doesn't duplicate conda activation
RUN sed -i '/conda\.sh\|conda activate/d' /etc/bash.bashrc 2>/dev/null || true

# 6. Add /opt/conda/bin to system PATH via /etc/environment (PAM, instant)
#    Replace the restrictive PATH line with one that includes conda
RUN sed -i 's|^export PATH="/usr/bin:/bin.*$|export PATH="/opt/conda/condabin:/opt/conda/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"|' /etc/environment

# 7. Add fast static conda env vars to profiles (no conda.sh overhead)
RUN printf '\n# Conda environment (static exports, no conda.sh overhead)\nexport CONDA_PREFIX=/opt/conda\nexport CONDA_DEFAULT_ENV=base\nexport CONDA_EXE=/opt/conda/bin/conda\nexport CONDA_PYTHON_EXE=/opt/conda/bin/python\nexport CONDA_SHLVL=1\n' >> /etc/profile

RUN printf '\n# Conda environment (static exports, no conda.sh overhead)\nexport CONDA_PREFIX=/opt/conda\nexport CONDA_DEFAULT_ENV=base\nexport CONDA_EXE=/opt/conda/bin/conda\nexport CONDA_PYTHON_EXE=/opt/conda/bin/python\nexport CONDA_SHLVL=1\n' >> /home/agentbox/.profile

RUN chown agentbox:agentbox /home/agentbox/.profile

# 8. Pre-install Dify CLI binary (avoids ~0.5s SFTP upload per sandbox creation)
RUN mkdir -p /opt/dify-cli/bin
COPY bin/dify-cli-linux-amd64 /opt/dify-cli/bin/dify
RUN chmod +x /opt/dify-cli/bin/dify && \
    chown agentbox:agentbox /opt/dify-cli/bin/dify

USER agentbox
