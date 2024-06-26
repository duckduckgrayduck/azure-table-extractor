"""
Extract tables from DocumentCloud documents with Azure Document Intelligence
"""
import os
import csv
import sys
import json
import zipfile
import requests
from PIL import Image
from documentcloud.addon import AddOn
from documentcloud.exceptions import APIError
from azure.ai.formrecognizer import DocumentAnalysisClient
from azure.core.credentials import AzureKeyCredential


class TableExtractor(AddOn):
    """Extract tables using Azure DI"""
    def calculate_cost(self, documents):
        """ Given a set of documents, counts the number of pages and returns a cost"""
        total_num_pages = 0
        for doc in documents:
            start_page = self.data.get("start_page", 1)
            end_page = self.data.get("end_page")
            last_page = 0
            if end_page <= doc.page_count:
                last_page = end_page
            else:
                last_page = doc.page_count
            pages_to_analyze = last_page - start_page + 1
            total_num_pages += pages_to_analyze
        cost = total_num_pages * 7
        print(cost)
        return cost


    def validate(self):
        """Validate that we can run the analysis"""

        if self.get_document_count() is None:
            self.set_message(
                "It looks like no documents were selected. Search for some or "
                "select them and run again."
            )
            sys.exit(0)
        if not self.org_id:
            self.set_message("No organization to charge.")
            sys.exit(0)
        ai_credit_cost = self.calculate_cost(
            self.get_documents()
        )
        try:
            self.charge_credits(ai_credit_cost)
        except ValueError:
            return False
        except APIError:
            return False
        return True

    def get_table_data(self, result, page_number):
        """Extract table data from the result of the poller"""
        table_data = []

        for table in result.tables:
            table_info = {
                "page_number": page_number,
                "cells": []
            }

            # Extract cells from the current table
            for cell in table.cells:
                cell_info = {
                    "row_index": cell.row_index,
                    "column_index": cell.column_index,
                    "content": cell.content
                }
                table_info["cells"].append(cell_info)

            # Append table info to the list
            table_data.append(table_info)

        return table_data

    def download_image(self, url, filename):
        """Download an image from a URL and save it locally."""
        response = requests.get(url, timeout=20)
        with open(filename, 'wb') as f:
            f.write(response.content)

    def convert_to_png(self, gif_filename, png_filename):
        """Convert a GIF image to PNG format."""
        gif_image = Image.open(gif_filename)
        gif_image.save(png_filename, 'PNG')

    def convert_to_csv(self, table_data):
        """Convert table data to CSV format"""
        csv_data = []
        for table_info in table_data:
            page_number = table_info["page_number"]
            max_row_index = max(cell["row_index"] for cell in table_info["cells"]) + 1
            rows = [[] for _ in range(max_row_index)]
            first_row = ["Page Number:", page_number]
            csv_data.append(first_row)
            for cell in table_info["cells"]:
                row_index = cell["row_index"]
                column_index = cell["column_index"]
                content = cell["content"]
                rows[row_index].append(content)
            for row_index, row_content in enumerate(rows):
                csv_row = row_content
                csv_data.append(csv_row)
            csv_data.extend([[], []])
        return csv_data

    def save_to_csv(self, csv_data, csv_filepath):
        with open(csv_filepath, "a", newline="", encoding="utf-8") as csvfile:
            writer = csv.writer(csvfile)
            for row in csv_data:
                writer.writerow(row)

    def main(self):
        """Validate, run the extraction on each document, save results in a zip file"""
        output_format = self.data.get("output_format", "json")
        start_page = self.data.get("start_page", 1)
        end_page = self.data.get("end_page", 1)

        if not self.validate():
            self.set_message(
                "You do not have sufficient AI credits to run this Add-On on this document set"
            )
            sys.exit(0)

        if end_page < start_page:
            self.set_message("The end page you provided is smaller than the start page, try again")
            sys.exit(0)
        if start_page < 1:
            self.set_message("Your start page is less than 1, please try again")
            sys.exit(0)

        # grab endpoint and API key for access from secrets
        key = os.environ.get("KEY")
        endpoint = os.environ.get("TOKEN")

        # authenticate
        document_analysis_client = DocumentAnalysisClient(
            endpoint=endpoint, credential=AzureKeyCredential(key)
        )

        zip_filename = "tables.zip"
        zipf = zipfile.ZipFile(zip_filename, "w")
        for document in self.get_documents():
            table_data = []
            outer_bound = end_page + 1
            if end_page > document.page_count:
                outer_bound = document.page_count + 1
            for page_number in range(start_page, outer_bound):
                image_url = document.get_large_image_url(page_number)
                gif_filename = f"{document.id}-page{page_number}.gif"
                self.download_image(image_url, gif_filename)
                png_filename = f"{document.id}-page{page_number}.png"
                self.convert_to_png(gif_filename, png_filename)
                with open(png_filename, "rb") as f:
                    poller = document_analysis_client.begin_analyze_document(
                        "prebuilt-layout", document=f
                    )
                result = poller.result()
                table_data.extend(self.get_table_data(result, page_number))

            if output_format == "json":
                table_data_json = json.dumps(table_data, indent=4)
                output_file_path = f"tables-{document.id}.json"
                zipf.writestr(output_file_path, table_data_json)
            if output_format == "csv":
                output_file_path = f"tables-{document.id}.csv"
                csv_data = self.convert_to_csv(table_data)
                self.save_to_csv(csv_data, output_file_path)
                zipf.write(output_file_path)
        zipf.close() 

        # Upload the zip file
        with open(zip_filename, "rb") as f:
            self.upload_file(f)


if __name__ == "__main__":
    TableExtractor().main()
