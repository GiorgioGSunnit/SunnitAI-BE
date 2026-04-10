FROM python:3.11
WORKDIR /app
COPY ./requirements.txt requirements.txt
RUN pip install --no-cache-dir --upgrade -r requirements.txt && \
    python -m spacy download it_core_news_lg

COPY . .

# ENTRYPOINT ["python", "requirement_extraction.py"]
# CMD ["--input_data", "LEGGE_28_giu_2024_cybersec.pdf", "--model_output", "output.json"]

CMD ["fastapi", "run", "app/main.py", "--port", "80"]