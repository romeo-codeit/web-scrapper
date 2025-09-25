import requests
from bs4 import BeautifulSoup
import json
import time
import os
import re
import argparse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import asyncio
import aiohttp

BASE_URL = "https://myschool.ng"

async def get_answer_and_explanation(session, answer_url, semaphore):
    """
    Gets the answer and explanation from the answer page asynchronously.
    """
    async with semaphore:
        try:
            async with session.get(answer_url, timeout=60) as response:
                response.raise_for_status()
                soup = BeautifulSoup(await response.text(), 'lxml')

                correct_answer = None
                explanation = None

                # Try to find the explanation first
                explanation_h5 = soup.find('h5', string=lambda t: t and 'Explanation' in t)
                if explanation_h5:
                    explanation_parts = []
                    for sibling in explanation_h5.find_next_siblings():
                        if sibling.name == 'h5':  # Stop at the next heading
                            break
                        # We get the text of the sibling, handling NavigableStrings and Tags
                        text = ''
                        if isinstance(sibling, str):
                            text = sibling.strip()
                        else:
                            text = sibling.get_text(strip=True)

                        if text:
                            explanation_parts.append(text)

                    explanation = '\n'.join(explanation_parts).strip()

                # For theory questions, the answer is the explanation.
                if 'type=theory' in answer_url:
                    correct_answer = explanation
                else:
                    # For obj questions, find the specific answer format
                    answer_h5 = soup.find('h5', class_='text-success')
                    if answer_h5 and 'Correct Answer:' in answer_h5.text:
                        correct_answer = answer_h5.text.replace('Correct Answer:', '').replace('Option', '').strip()

                year = None
                # Find any 'a' tag that contains the exam type and a year
                year_pattern = re.compile(r'(?:NECO|WAEC|JAMB)\s*(\d{4})')
                year_tag = soup.find('a', string=year_pattern)
                if year_tag:
                    match = year_pattern.search(year_tag.text)
                    if match:
                        year = match.group(1)

                return correct_answer, explanation, year, answer_url
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            print(f"Error fetching answer from {answer_url}: {e}")
            with open("failed_urls.txt", "a") as f:
                f.write(f"answer:{answer_url}\n")
            return None, None, None, answer_url

async def scrape_page(session, url, semaphore, all_questions):
    """
    Scrapes a single page of questions and then fetches all answers concurrently.
    """
    try:
        print(f"Scraping {url}")
        async with session.get(url, timeout=60) as response:
            response.raise_for_status()
            soup = BeautifulSoup(await response.text(), 'lxml')

            question_items = soup.find_all('div', class_='question-item')
            tasks = []

            for item in question_items:
                question_number_tag = item.find(class_='question_sn')
                question_desc_tag = item.find(class_='question-desc')
                options_list = item.find('ul', class_='list-unstyled')
                answer_link_tag = item.find('a', class_='btn-outline-danger')

                if not all([question_number_tag, question_desc_tag, answer_link_tag]):
                    continue

                question_number = question_number_tag.text.strip()
                question_desc = question_desc_tag.text.strip()

                options = {}
                if options_list:
                    for option in options_list.find_all('li'):
                        key_tag = option.find('strong')
                        if not key_tag:
                            continue
                        key = key_tag.text.strip().replace('.', '')
                        value = option.text.replace(key_tag.text, '').strip()
                        options[key] = value

                answer_link = answer_link_tag['href']
                if not answer_link.startswith('http'):
                    answer_link = f"{BASE_URL}{answer_link}"

                question_data = {
                    'number': question_number,
                    'text': question_desc,
                    'options': options,
                    'answer_url': answer_link, # Store the answer_url
                }
                tasks.append((get_answer_and_explanation(session, answer_link, semaphore), question_data))

            answer_results = await asyncio.gather(*(task for task, data in tasks))

            for i, (correct_answer, explanation, year, answer_url) in enumerate(answer_results):
                question_data = tasks[i][1]
                question_data.update({
                    'correct_answer': correct_answer,
                    'explanation': explanation,
                    'year': year,
                })
                all_questions.append(question_data)

            next_page_link = soup.select_one('li.page-item a[rel="next"]')
            if next_page_link:
                next_page_url = next_page_link['href']
                if not next_page_url.startswith('http'):
                    next_page_url = f"{BASE_URL}{next_page_url}"
                return next_page_url
            else:
                return None

    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        print(f"Error fetching questions from {url}: {e}")
        with open("failed_urls.txt", "a") as f:
            f.write(f"questions:{url}\n")
        return None

async def scrape_all_pages(session, start_url, page_limit=0):
    """
    Scrapes all pages for a subject, following pagination.
    """
    all_questions = []
    semaphore = asyncio.Semaphore(50)
    next_page_url = start_url
    pages_scraped = 0

    while next_page_url:
        if page_limit > 0 and pages_scraped >= page_limit:
            print(f"Page limit of {page_limit} reached. Stopping.")
            break
        next_page_url = await scrape_page(session, next_page_url, semaphore, all_questions)
        pages_scraped += 1


    return all_questions

def sanitize_filename(filename):
    """
    Sanitizes a string to be used as a filename.
    """
    return re.sub(r'[^a-zA-Z0-9_.-]', '', filename)

async def main():
    parser = argparse.ArgumentParser(description="Scrape past exam questions from myschool.ng")
    parser.add_argument("subject_name", type=str, nargs='?', default=None, help="The name of the subject to scrape (e.g., 'Mathematics').")
    parser.add_argument("--exam_type", type=str, choices=['waec', 'jamb', 'neco'], help="The type of exam (waec, jamb, or neco).")
    parser.add_argument("--start_year", type=int, default=2010, help="The starting year for scraping.")
    parser.add_argument("--end_year", type=int, default=2024, help="The ending year for scraping.")
    parser.add_argument("--strict", action='store_true', help="If set, only save questions that have both options and a correct answer.")
    parser.add_argument("--retry-failed", action='store_true', help="If set, retry scraping from a list of failed URLs.")
    parser.add_argument("--page_limit", type=int, default=0, help="Limit the number of pages to scrape.")
    parser.add_argument("--start_page", type=int, default=1, help="The page number to start scraping from.")

    args = parser.parse_args()

    if args.retry_failed:
        if args.subject_name or args.exam_type:
            parser.error("--retry-failed cannot be used with subject_name or --exam_type.")
        await retry_failed_urls(args.start_year, args.end_year, args.strict)
    elif args.subject_name and args.exam_type:
        await scrape_subject(args.subject_name, args.exam_type, args.start_year, args.end_year, args.strict, args.page_limit, args.start_page)
    else:
        parser.error("subject_name and --exam_type are required unless --retry-failed is used.")

async def scrape_subject(subject_name, exam_type, start_year, end_year, strict_mode=False, page_limit=0, start_page=1):
    """
    Scrapes past questions for a specific subject and exam type within a given year range.
    """
    classroom_url = f"{BASE_URL}/classroom"
    output_dir = "past_questions"

    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    paper_types = ['obj', 'theory']

    async with aiohttp.ClientSession() as session:
        with requests.Session() as req_session:
            retries = Retry(total=5, backoff_factor=1, status_forcelist=[500, 502, 503, 504])
            req_session.mount('https://', HTTPAdapter(max_retries=retries))
            req_session.mount('http://', HTTPAdapter(max_retries=retries))
            
            response = req_session.get(classroom_url)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, 'lxml')
            all_subjects = {}
            subject_elements = soup.find_all('li', class_='media')
            for subject_element in subject_elements:
                media_body = subject_element.find('div', class_='media-body')
                if media_body:
                    h5 = media_body.find('h5')
                    if h5:
                        subject_link = h5.find('a')
                        if subject_link:
                            s_name = subject_link.text.strip()
                            s_url = subject_link['href']
                            all_subjects[s_name] = s_url

        if subject_name not in all_subjects:
            print(f"Error: Subject '{subject_name}' not found on the website.")
            print(f"Available subjects are: {', '.join(all_subjects.keys())}")
            return

        subject_url = all_subjects[subject_name]
        print(f"Found URL for {subject_name}: {subject_url}")

        for paper_type in paper_types:
            print(f"\n--- Scraping {paper_type.capitalize()} Questions for {exam_type.upper()} ---")

            if '?' not in subject_url:
                scrape_url = f"{subject_url}?exam_type={exam_type}&type={paper_type}"
            else:
                scrape_url = f"{subject_url}&exam_type={exam_type}&type={paper_type}"

            if start_page > 1:
                scrape_url = f"{scrape_url}&page={start_page}"

            questions = await scrape_all_pages(session, scrape_url, page_limit)
            print(f"Found {len(questions)} {paper_type} questions for {subject_name}")

            questions_by_year_for_type = {}
            for q in questions:
                year_str = q.get('year')
                if not year_str or not year_str.isdigit():
                    continue

                year = int(year_str)
                if start_year <= year <= end_year:
                    if strict_mode and (not q.get('options') or not q.get('correct_answer')):
                        print(f"  [Strict Mode] Skipping question number {q.get('number')} for year {year} due to missing data.")
                        continue

                    if year not in questions_by_year_for_type:
                        questions_by_year_for_type[year] = []
                    questions_by_year_for_type[year].append(q)

            for year, year_questions in questions_by_year_for_type.items():
                sanitized_subject_name = sanitize_filename(subject_name)
                filename = f"{exam_type.upper()}_{sanitized_subject_name}_{year}_{paper_type}.json"
                filepath = os.path.join(output_dir, filename)

                # Load existing data if the file exists, otherwise start with an empty list
                existing_questions = []
                if os.path.exists(filepath):
                    with open(filepath, 'r') as f:
                        try:
                            existing_questions = json.load(f)
                        except json.JSONDecodeError:
                            pass # File is empty or corrupt, start fresh

                # Create a set of existing question numbers for quick lookup
                existing_question_numbers = {q['number'] for q in existing_questions}

                # Add only new questions
                for q in year_questions:
                    if q['number'] not in existing_question_numbers:
                        existing_questions.append(q)

                with open(filepath, 'w') as f:
                    json.dump(existing_questions, f, indent=2)
                print(f"Saved {len(existing_questions)} total questions to {filepath}")

    print("\nScraping complete for this subject.")

async def retry_failed_urls(start_year, end_year, strict_mode=False):
    """
    Retries scraping URLs that have previously failed and were logged.
    """
    failed_urls_file = "failed_urls.txt"
    if not os.path.exists(failed_urls_file):
        print("No failed URLs file found. Nothing to retry.")
        return

    with open(failed_urls_file, 'r') as f:
        failed_urls = [line.strip() for line in f.readlines()]

    if not failed_urls:
        print("No failed URLs to retry.")
        return

    print(f"Retrying {len(failed_urls)} failed URLs...")

    newly_scraped_questions = []
    recovered_answers = []
    
    async with aiohttp.ClientSession() as session:
        semaphore = asyncio.Semaphore(10)
        question_page_urls = [url.split(':', 1)[1] for url in failed_urls if url.startswith('questions:')]
        answer_urls = [url.split(':', 1)[1] for url in failed_urls if url.startswith('answer:')]

        # Retry question pages
        page_tasks = [scrape_page(session, url, semaphore, newly_scraped_questions) for url in question_page_urls]
        await asyncio.gather(*page_tasks)

        # Retry answer URLs
        answer_tasks = [get_answer_and_explanation(session, url, semaphore) for url in answer_urls]
        recovered_answers = await asyncio.gather(*answer_tasks)

    # Process newly scraped questions from failed pages
    if newly_scraped_questions:
        print(f"\nProcessing {len(newly_scraped_questions)} newly scraped questions...")
        # This part is still tricky as we don't know the subject/exam_type from the URL.
        # We will save them to a generic file. A better approach is to add more context to failed_urls.txt
        questions_by_year = {}
        for q in newly_scraped_questions:
            year_str = q.get('year')
            if not year_str or not year_str.isdigit(): continue
            year = int(year_str)
            if year not in questions_by_year: questions_by_year[year] = []
            questions_by_year[year].append(q)
        
        output_dir = "past_questions"
        for year, questions in questions_by_year.items():
            filename = f"recovered_questions_{year}.json"
            filepath = os.path.join(output_dir, filename)
            with open(filepath, 'w') as f:
                json.dump(questions, f, indent=2)
            print(f"Saved {len(questions)} recovered questions to {filepath}")


    # Process recovered answers
    if recovered_answers:
        print(f"\nProcessing {len(recovered_answers)} recovered answers...")
        output_dir = "past_questions"
        for correct_answer, explanation, year, answer_url in recovered_answers:
            if not all([correct_answer, year, answer_url]):
                print(f"Could not recover complete answer for {answer_url}. Skipping.")
                continue

            year = int(year)
            updated = False
            for filename in os.listdir(output_dir):
                if f"_{year}_" in filename:
                    filepath = os.path.join(output_dir, filename)
                    try:
                        with open(filepath, 'r+') as f:
                            questions = json.load(f)
                            for i, q in enumerate(questions):
                                if q.get('answer_url') == answer_url:
                                    questions[i]['correct_answer'] = correct_answer
                                    questions[i]['explanation'] = explanation
                                    f.seek(0)
                                    json.dump(questions, f, indent=2)
                                    f.truncate()
                                    print(f"Updated question in {filename} for answer {answer_url}")
                                    updated = True
                                    break
                            if updated: break
                    except (json.JSONDecodeError, IOError) as e:
                        print(f"Error processing file {filepath}: {e}")
            if not updated:
                print(f"Could not find a matching question for recovered answer: {answer_url}")

    # Clear the failed URLs file
    open(failed_urls_file, 'w').close()
    print("\nFinished retrying failed URLs.")


if __name__ == "__main__":
    # On Windows, the default event loop policy can cause issues with aiohttp.
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())