const axios = require('axios');
const xml2js = require('xml2js');
require('dotenv').config();

const { CLIENT_IP, WHM_API_KEY } = process.env;

async function getBandwidth() {
  const WHM_API_URL = `https://${CLIENT_IP}:2087/json-api/showbw?api.version=1`;
  try {
    const response = await axios.get(WHM_API_URL, {
      headers: {
        Authorization: `WHM root:${WHM_API_KEY}`,
      },
      httpsAgent: new (require('https').Agent)({ rejectUnauthorized: false }),
    });
    if (response.data) {
      return response.data;
    } else {
      throw new Error('Invalid response format or data not found');
    }
  } catch (error) {
    console.error('Error fetching bandwidth:', error.message);
  }
}

module.exports = { getBandwidth };
